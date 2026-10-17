using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.IO;
using System.Management;
using System.Runtime.InteropServices;
using System.Threading;
using System.Drawing;
using System.Windows.Forms;
using System.Xml.Linq;
using System.Xml;
using System.Globalization;

internal static class MonitorBrightnessSync
{
    private const int PollMilliseconds = 3000;
    private const int Hysteresis = 1;
    private static List<MonitorRule> Rules;
    private static readonly string SettingsDirectory = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "MonitorBrightnessSync");
    private static readonly Dictionary<string, DimLayer> DimLayers = new Dictionary<string, DimLayer>();
    private static readonly string LogPath = Path.Combine(
        SettingsDirectory, "sync.log"
    );

    [STAThread]
    private static void Main()
    {
        SetProcessDPIAware();
        bool createdNew;
        using (var mutex = new Mutex(true, "Local\\MonitorBrightnessSyncNative", out createdNew))
        {
            if (!createdNew) return;
            try
            {
                var xmlSettings = new XmlReaderSettings();
                xmlSettings.DtdProcessing = DtdProcessing.Prohibit;
                xmlSettings.XmlResolver = null;
                using (XmlReader reader = XmlReader.Create(Path.Combine(SettingsDirectory, "config.xml"), xmlSettings))
                    Rules = ParseSettings(XDocument.Load(reader));
            }
            catch (Exception ex) { Log("設定を読み込めないため終了: " + ex.Message); return; }
            Log("起動しました。");
            while (true)
            {
                try
                {
                    int internalBrightness = GetInternalBrightness();
                    SyncDisplays(internalBrightness);
                }
                catch (Exception ex)
                {
                    Log("エラー: " + ex.Message);
                }
                for (int elapsed = 0; elapsed < PollMilliseconds; elapsed += 50)
                {
                    Application.DoEvents();
                    Thread.Sleep(50);
                }
            }
        }
    }

    private static int GetInternalBrightness()
    {
        var scope = new ManagementScope(@"\\.\root\wmi");
        scope.Connect();
        using (var searcher = new ManagementObjectSearcher(
            scope,
            new ObjectQuery("SELECT CurrentBrightness, Active FROM WmiMonitorBrightness WHERE Active=True")
        ))
        using (ManagementObjectCollection results = searcher.Get())
        {
            foreach (ManagementObject item in results)
                return Convert.ToInt32(item["CurrentBrightness"]);
        }
        throw new InvalidOperationException("The active internal display brightness was not available.");
    }

    private static void SyncDisplays(int internalBrightness)
    {
        var activeDimDevices = new HashSet<string>();
        foreach (PhysicalDisplay display in EnumerateDisplays())
        {
            try
            {
                string id = display.DeviceId ?? "";
                foreach (MonitorRule rule in Rules)
                {
                    if (id.IndexOf("MONITOR\\" + rule.Id + "\\", StringComparison.OrdinalIgnoreCase) < 0) continue;
                    if (rule.Hardware)
                        SetHardwareBrightnessIfNeeded(display, Clamp((int)Math.Round(internalBrightness * rule.Scale), 0, 100), rule.Name);
                    if (rule.BlueGain >= 0) SetVcpIfNeeded(display.Handle, 0x1A, rule.BlueGain, rule.Name);
                    if (rule.SoftwareFollow)
                    {
                        activeDimDevices.Add(display.LogicalDevice);
                        int target = Math.Max(rule.Minimum, internalBrightness);
                        SetGammaIfNeeded(display.LogicalDevice, Math.Max(50, target), rule.Name);
                        SetDimLayer(display.LogicalDevice, target);
                    }
                    else if (rule.Gamma >= 0) SetGammaIfNeeded(display.LogicalDevice, rule.Gamma, rule.Name);
                    break;
                }
            }
            catch (Exception ex)
            {
                Log("画面 " + display.DeviceId + ": " + ex.Message);
            }
            finally
            {
                if (display.Handle != IntPtr.Zero) DestroyPhysicalMonitor(display.Handle);
            }
        }
        foreach (var entry in DimLayers)
            if (!activeDimDevices.Contains(entry.Key)) entry.Value.Hide();
    }

    private static void SetHardwareBrightnessIfNeeded(PhysicalDisplay display, int target, string label)
    {
        uint minimum, current, maximum;
        if (!GetMonitorBrightness(display.Handle, out minimum, out current, out maximum)) return;
        int currentPercent = maximum <= minimum
            ? 0
            : (int)Math.Round(100.0 * (current - minimum) / (maximum - minimum));
        target = Clamp(target, 0, 100);
        if (Math.Abs(currentPercent - target) <= Hysteresis) return;
        uint raw = minimum + (uint)Math.Round((maximum - minimum) * (target / 100.0));
        if (!SetMonitorBrightness(display.Handle, raw) && !SetVCPFeature(display.Handle, 0x10, raw))
            throw new Win32Exception(Marshal.GetLastWin32Error());
        Log(String.Format("{0} バックライト: {1}% -> {2}%", label, currentPercent, target));
    }

    private static void SetVcpIfNeeded(IntPtr handle, byte code, int target, string label)
    {
        int type;
        uint current, maximum;
        if (!GetVCPFeatureAndVCPFeatureReply(handle, code, out type, out current, out maximum)) return;
        if (current == (uint)target) return;
        if (!SetVCPFeature(handle, code, (uint)target))
            throw new Win32Exception(Marshal.GetLastWin32Error());
        Log(String.Format("{0}: {1} -> {2}", label, current, target));
    }

    private static void SetGammaIfNeeded(string logicalDevice, int target, string label)
    {
        int current = GetGammaPercent(logicalDevice);
        if (Math.Abs(current - target) <= Hysteresis) return;
        SetGammaPercent(logicalDevice, target);
        Log(String.Format("{0} ソフトウェア補正: {1}% -> {2}%", label, current, target));
    }

    private static int GetGammaPercent(string device)
    {
        IntPtr dc = CreateDC("DISPLAY", device, null, IntPtr.Zero);
        if (dc == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error());
        ushort[] ramp = new ushort[768];
        GCHandle pinned = GCHandle.Alloc(ramp, GCHandleType.Pinned);
        try
        {
            if (!GetDeviceGammaRamp(dc, pinned.AddrOfPinnedObject()))
                throw new Win32Exception(Marshal.GetLastWin32Error());
            uint max = Math.Max(ramp[255], Math.Max(ramp[511], ramp[767]));
            return (int)Math.Round(max * 100.0 / 65535.0);
        }
        finally
        {
            pinned.Free();
            DeleteDC(dc);
        }
    }

    private static void SetGammaPercent(string device, int percent)
    {
        percent = Clamp(percent, 50, 100);
        double scale = percent / 100.0;
        ushort[] ramp = new ushort[768];
        for (int channel = 0; channel < 3; channel++)
            for (int i = 0; i < 256; i++)
                ramp[channel * 256 + i] = (ushort)Math.Round(i * 257.0 * scale);

        IntPtr dc = CreateDC("DISPLAY", device, null, IntPtr.Zero);
        if (dc == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error());
        GCHandle pinned = GCHandle.Alloc(ramp, GCHandleType.Pinned);
        try
        {
            if (!SetDeviceGammaRamp(dc, pinned.AddrOfPinnedObject()))
                throw new Win32Exception(Marshal.GetLastWin32Error());
        }
        finally
        {
            pinned.Free();
            DeleteDC(dc);
        }
    }

    private static List<PhysicalDisplay> EnumerateDisplays()
    {
        var result = new List<PhysicalDisplay>();
        MonitorEnumProc callback = delegate(IntPtr logicalMonitor, IntPtr hdc, IntPtr rect, IntPtr data)
        {
            uint count;
            if (!GetNumberOfPhysicalMonitorsFromHMONITOR(logicalMonitor, out count) || count == 0)
                return true;

            var info = new MONITORINFOEX();
            info.cbSize = Marshal.SizeOf(typeof(MONITORINFOEX));
            if (!GetMonitorInfo(logicalMonitor, ref info)) return true;

            var devices = new PHYSICAL_MONITOR[count];
            if (!GetPhysicalMonitorsFromHMONITOR(logicalMonitor, count, devices)) return true;

            var displayDevice = new DISPLAY_DEVICE();
            displayDevice.cb = Marshal.SizeOf(typeof(DISPLAY_DEVICE));
            EnumDisplayDevices(info.szDevice, 0, ref displayDevice, 0);
            foreach (PHYSICAL_MONITOR physical in devices)
            {
                result.Add(new PhysicalDisplay
                {
                    Handle = physical.hPhysicalMonitor,
                    LogicalDevice = info.szDevice,
                    DeviceId = displayDevice.DeviceID
                });
            }
            return true;
        };
        if (!EnumDisplayMonitors(IntPtr.Zero, IntPtr.Zero, callback, IntPtr.Zero))
            throw new InvalidOperationException("EnumDisplayMonitors failed.");
        return result;
    }

    private static void SetDimLayer(string device, int target)
    {
        DimLayer layer;
        if (target >= 50)
        {
            if (DimLayers.TryGetValue(device, out layer)) layer.Hide();
            return;
        }
        Screen screen = null;
        foreach (Screen candidate in Screen.AllScreens)
            if (candidate.DeviceName == device) screen = candidate;
        if (screen == null) return;
        if (!DimLayers.TryGetValue(device, out layer))
        {
            layer = new DimLayer();
            DimLayers.Add(device, layer);
        }
        layer.Bounds = screen.Bounds;
        layer.Opacity = 1.0 - Math.Max(5, target) / 50.0;
        if (!layer.Visible)
        {
            layer.Show();
            Log("暗色レイヤーを有効化: " + target + "%");
        }
    }

    private sealed class DimLayer : Form
    {
        public DimLayer()
        {
            FormBorderStyle = FormBorderStyle.None;
            ShowInTaskbar = false;
            StartPosition = FormStartPosition.Manual;
            BackColor = Color.Black;
            TopMost = true;
        }
        protected override bool ShowWithoutActivation { get { return true; } }
        protected override CreateParams CreateParams
        {
            get
            {
                CreateParams value = base.CreateParams;
                value.ExStyle |= 0x08000000 | 0x00000020 | 0x00000080;
                return value;
            }
        }
    }

    private sealed class MonitorRule
    {
        public string Id, Name;
        public bool Hardware, SoftwareFollow;
        public double Scale;
        public int Gamma, Minimum, BlueGain;
    }

    private static List<MonitorRule> ParseSettings(XDocument document)
    {
        if (document.Root == null || document.Root.Name != "monitors")
            throw new FormatException("ルート要素はmonitorsにしてください");
        var result = new List<MonitorRule>();
        var ids = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (XElement node in document.Root.Elements())
        {
            if (node.Name != "monitor") throw new FormatException("未対応の設定要素です");
            var rule = new MonitorRule();
            rule.Id = (string)node.Attribute("id") ?? "";
            if (!System.Text.RegularExpressions.Regex.IsMatch(rule.Id, "^[A-Za-z0-9_-]{3,32}$") || !ids.Add(rule.Id))
                throw new FormatException("モニターIDが不正または重複しています");
            rule.Name = (string)node.Attribute("name") ?? "外部画面";
            rule.Hardware = (bool?)node.Attribute("hardware") ?? false;
            rule.SoftwareFollow = (bool?)node.Attribute("softwareFollow") ?? false;
            rule.Scale = double.Parse((string)node.Attribute("scale") ?? "1", CultureInfo.InvariantCulture);
            rule.Gamma = (int?)node.Attribute("gamma") ?? -1;
            rule.Minimum = (int?)node.Attribute("minimum") ?? 5;
            rule.BlueGain = (int?)node.Attribute("blueGain") ?? -1;
            if (double.IsNaN(rule.Scale) || double.IsInfinity(rule.Scale) || rule.Scale < 0 || rule.Scale > 1
                || (rule.Gamma != -1 && (rule.Gamma < 50 || rule.Gamma > 100))
                || rule.Minimum < 5 || rule.Minimum > 100
                || rule.BlueGain < -1 || rule.BlueGain > 100
                || (rule.SoftwareFollow && rule.Gamma != -1))
                throw new FormatException("明るさ設定が範囲外または競合しています");
            foreach (XAttribute attribute in node.Attributes())
                if (Array.IndexOf(new string[] { "id", "name", "hardware", "softwareFollow", "scale", "gamma", "minimum", "blueGain" }, attribute.Name.ToString()) < 0)
                    throw new FormatException("未対応の設定属性です");
            result.Add(rule);
        }
        if (result.Count == 0) throw new FormatException("同期対象がありません");
        return result;
    }

    private static int Clamp(int value, int minimum, int maximum)
    {
        return Math.Max(minimum, Math.Min(maximum, value));
    }

    private static void Log(string message)
    {
        try
        {
            Directory.CreateDirectory(SettingsDirectory);
            if (File.Exists(LogPath) && new FileInfo(LogPath).Length > 1024 * 1024)
                File.Delete(LogPath);
            File.AppendAllText(LogPath, DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss ") + message + Environment.NewLine);
        }
        catch { }
    }

    private sealed class PhysicalDisplay
    {
        public IntPtr Handle;
        public string LogicalDevice;
        public string DeviceId;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct RECT { public int Left, Top, Right, Bottom; }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Auto)]
    private struct MONITORINFOEX
    {
        public int cbSize;
        public RECT rcMonitor;
        public RECT rcWork;
        public uint dwFlags;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string szDevice;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct PHYSICAL_MONITOR
    {
        public IntPtr hPhysicalMonitor;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)] public string description;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct DISPLAY_DEVICE
    {
        public int cb;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string DeviceName;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)] public string DeviceString;
        public uint StateFlags;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)] public string DeviceID;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)] public string DeviceKey;
    }

    private delegate bool MonitorEnumProc(IntPtr monitor, IntPtr hdc, IntPtr rect, IntPtr data);

    [DllImport("user32.dll")] private static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] private static extern bool EnumDisplayMonitors(IntPtr hdc, IntPtr clip, MonitorEnumProc callback, IntPtr data);
    [DllImport("user32.dll", CharSet = CharSet.Auto)] private static extern bool GetMonitorInfo(IntPtr monitor, ref MONITORINFOEX info);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern bool EnumDisplayDevices(string device, uint index, ref DISPLAY_DEVICE displayDevice, uint flags);
    [DllImport("dxva2.dll", SetLastError = true)] private static extern bool GetNumberOfPhysicalMonitorsFromHMONITOR(IntPtr monitor, out uint count);
    [DllImport("dxva2.dll", SetLastError = true)] private static extern bool GetPhysicalMonitorsFromHMONITOR(IntPtr monitor, uint count, [Out] PHYSICAL_MONITOR[] monitors);
    [DllImport("dxva2.dll", SetLastError = true)] private static extern bool DestroyPhysicalMonitor(IntPtr monitor);
    [DllImport("dxva2.dll", SetLastError = true)] private static extern bool GetMonitorBrightness(IntPtr monitor, out uint minimum, out uint current, out uint maximum);
    [DllImport("dxva2.dll", SetLastError = true)] private static extern bool SetMonitorBrightness(IntPtr monitor, uint brightness);
    [DllImport("dxva2.dll", SetLastError = true)] private static extern bool GetVCPFeatureAndVCPFeatureReply(IntPtr monitor, byte code, out int type, out uint current, out uint maximum);
    [DllImport("dxva2.dll", SetLastError = true)] private static extern bool SetVCPFeature(IntPtr monitor, byte code, uint value);
    [DllImport("gdi32.dll", CharSet = CharSet.Unicode)] private static extern IntPtr CreateDC(string driver, string device, string output, IntPtr initData);
    [DllImport("gdi32.dll")] private static extern bool DeleteDC(IntPtr dc);
    [DllImport("gdi32.dll")] private static extern bool GetDeviceGammaRamp(IntPtr dc, IntPtr ramp);
    [DllImport("gdi32.dll")] private static extern bool SetDeviceGammaRamp(IntPtr dc, IntPtr ramp);
}
