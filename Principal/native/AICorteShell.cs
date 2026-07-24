using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Win32;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

internal static class Program
{
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool SetDllDirectory(string path);

    [STAThread]
    private static void Main()
    {
        string runtime = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "Principal", "native", "runtime");
        SetDllDirectory(runtime);
        AppDomain.CurrentDomain.AssemblyResolve += delegate(object sender, ResolveEventArgs args)
        {
            string candidate = Path.Combine(runtime, new AssemblyName(args.Name).Name + ".dll");
            return File.Exists(candidate) ? Assembly.LoadFrom(candidate) : null;
        };
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        Application.Run(new MainForm());
    }
}

internal sealed class MainForm : Form
{
    private const string DashboardUrl = "http://127.0.0.1:8787";
    private readonly WebView2 browser = new WebView2();
    private readonly JavaScriptSerializer json = new JavaScriptSerializer();
    private readonly object logLock = new object();
    private readonly string bundleRoot = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar);
    private string selectedRoot;
    private Process backendProcess;

    public MainForm()
    {
        Text = "AICorte";
        MinimumSize = new System.Drawing.Size(980, 680);
        StartPosition = FormStartPosition.CenterScreen;
        WindowState = FormWindowState.Maximized;
        BackColor = System.Drawing.Color.FromArgb(11, 13, 14);
        browser.Dock = DockStyle.Fill;
        Controls.Add(browser);
        Shown += async delegate { await InitializeAsync(); };
        FormClosing += HandleFormClosing;
    }

    private void HandleFormClosing(object sender, FormClosingEventArgs args)
    {
        if (backendProcess == null) return;
        try
        {
            if (!backendProcess.HasExited)
            {
                backendProcess.Kill();
                backendProcess.WaitForExit(3000);
            }
        }
        catch { }
        finally
        {
            backendProcess.Dispose();
            backendProcess = null;
        }
    }

    private async Task InitializeAsync()
    {
        selectedRoot = ReadSavedRoot();
        string dataRoot = IsInstallationReady(selectedRoot) ? selectedRoot : bundleRoot;
        string profile = Path.Combine(dataRoot, "Principal", "state", "webview2-profile");
        Directory.CreateDirectory(profile);
        try
        {
            CoreWebView2Environment environment = await CoreWebView2Environment.CreateAsync(null, profile);
            await browser.EnsureCoreWebView2Async(environment);
        }
        catch (Exception error)
        {
            DialogResult choice = MessageBox.Show(
                "O Microsoft Edge WebView2 Runtime nao esta disponivel. Deseja baixar e instalar o componente oficial agora?\n\n" + error.Message,
                "AICorte",
                MessageBoxButtons.YesNo,
                MessageBoxIcon.Warning);
            if (choice == DialogResult.Yes && InstallWebView2Runtime())
            {
                Application.Restart();
                Close();
                return;
            }
            Close();
            return;
        }

        browser.CoreWebView2.Settings.AreDevToolsEnabled = false;
        browser.CoreWebView2.Settings.AreDefaultContextMenusEnabled = false;
        browser.CoreWebView2.Settings.IsStatusBarEnabled = false;
        browser.CoreWebView2.Settings.IsZoomControlEnabled = true;
        browser.CoreWebView2.WebMessageReceived += HandleMessage;
        browser.CoreWebView2.NewWindowRequested += HandleNewWindow;

        if (IsInstallationReady(selectedRoot))
            await OpenDashboardAsync();
        else
            OpenSetup();
    }

    private void OpenSetup()
    {
        string setup = Path.Combine(bundleRoot, "Principal", "native", "setup.html");
        if (!File.Exists(setup))
            throw new FileNotFoundException("Tela de configuracao ausente", setup);
        browser.CoreWebView2.SetVirtualHostNameToFolderMapping(
            "setup.aicorte.local",
            Path.GetDirectoryName(setup),
            CoreWebView2HostResourceAccessKind.DenyCors);
        browser.CoreWebView2.Navigate("https://setup.aicorte.local/setup.html");
    }

    private async void HandleMessage(object sender, CoreWebView2WebMessageReceivedEventArgs args)
    {
        try
        {
            Dictionary<string, object> message = json.Deserialize<Dictionary<string, object>>(args.WebMessageAsJson);
            string action = message.ContainsKey("action") ? Convert.ToString(message["action"]) : "";
            if (action == "pick-folder") PickFolder();
            else if (action == "check") SendEnvironment();
            else if (action == "install") await InstallEnvironmentAsync();
            else if (action == "open-dashboard") await OpenDashboardAsync();
            else if (action == "open-external") OpenExternal(Convert.ToString(message["url"]));
        }
        catch (Exception error)
        {
            Post(new Dictionary<string, object> {
                { "event", "error" }, { "message", error.Message }
            });
        }
    }

    private void PickFolder()
    {
        using (FolderBrowserDialog dialog = new FolderBrowserDialog())
        {
            dialog.Description = "Selecione ou crie a pasta raiz do AICorte";
            dialog.ShowNewFolderButton = true;
            dialog.SelectedPath = Directory.Exists(selectedRoot) ? selectedRoot : bundleRoot;
            if (dialog.ShowDialog(this) != DialogResult.OK) return;
            selectedRoot = Path.GetFullPath(dialog.SelectedPath).TrimEnd(Path.DirectorySeparatorChar);
            if (String.IsNullOrWhiteSpace(Path.GetPathRoot(selectedRoot)) || selectedRoot.StartsWith("\\\\"))
                throw new InvalidOperationException("Escolha uma pasta em um disco local do Windows.");
            Post(new Dictionary<string, object> {
                { "event", "folder" }, { "root", selectedRoot }
            });
            SendEnvironment();
        }
    }

    private void SendEnvironment()
    {
        string root = String.IsNullOrWhiteSpace(selectedRoot) ? "" : selectedRoot;
        bool hasRoot = root.Length > 0 && Directory.Exists(root);
        string runtime = hasRoot ? Path.Combine(root, "app", "runtime") : "";
        List<Dictionary<string, object>> dependencies = new List<Dictionary<string, object>> {
            Dependency("root", "Pasta de dados", hasRoot, root),
            Dependency("python", "Python portatil", hasRoot && FindPython(root) != null, hasRoot ? Path.Combine(runtime, "uv-python") : ""),
            Dependency("git", "Git portatil", hasRoot && File.Exists(Path.Combine(runtime, "git", "current", "cmd", "git.exe")), hasRoot ? Path.Combine(runtime, "git") : ""),
            Dependency("webview2", "Chromium (WebView2)", browser.CoreWebView2 != null, "Runtime nativo do Windows"),
            Dependency("docker", "Docker CLI", hasRoot && File.Exists(Path.Combine(runtime, "docker", "docker.exe")), hasRoot ? Path.Combine(runtime, "docker") : ""),
            Dependency("compose", "Docker Compose", hasRoot && File.Exists(Path.Combine(runtime, "docker", "docker-compose.exe")), hasRoot ? Path.Combine(runtime, "docker") : ""),
            Dependency("engine", "Docker Engine local", DockerReady(root), "WSL2 em " + (hasRoot ? Path.Combine(runtime, "docker-wsl") : ""))
        };
        Post(new Dictionary<string, object> {
            { "event", "environment" }, { "root", root }, { "dependencies", dependencies },
            { "ready", hasRoot && dependencies.All(item => Convert.ToBoolean(item["ok"])) }
        });
    }

    private static Dictionary<string, object> Dependency(string id, string name, bool ok, string detail)
    {
        return new Dictionary<string, object> {
            { "id", id }, { "name", name }, { "ok", ok }, { "detail", detail }
        };
    }

    private async Task InstallEnvironmentAsync()
    {
        if (String.IsNullOrWhiteSpace(selectedRoot))
            throw new InvalidOperationException("Escolha a pasta do AICorte antes de instalar.");
        Directory.CreateDirectory(selectedRoot);
        CopyPayload(selectedRoot);
        string script = Path.Combine(selectedRoot, "Principal", "scripts", "bootstrap-environment.ps1");
        if (!File.Exists(script)) throw new FileNotFoundException("Instalador do ambiente ausente", script);
        Post(new Dictionary<string, object> { { "event", "install-started" } });

        int exitCode = await Task.Run(delegate
        {
            ProcessStartInfo start = new ProcessStartInfo {
                FileName = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows), "System32", "WindowsPowerShell", "v1.0", "powershell.exe"),
                Arguments = "-NoProfile -ExecutionPolicy Bypass -File \"" + script + "\" -Root \"" + selectedRoot + "\"",
                WorkingDirectory = selectedRoot,
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8,
                StandardErrorEncoding = Encoding.UTF8
            };
            using (Process process = Process.Start(start))
            {
                process.OutputDataReceived += delegate(object s, DataReceivedEventArgs e) { if (e.Data != null) PostLog(e.Data); };
                process.ErrorDataReceived += delegate(object s, DataReceivedEventArgs e) { if (e.Data != null) PostLog(e.Data); };
                process.BeginOutputReadLine();
                process.BeginErrorReadLine();
                process.WaitForExit();
                return process.ExitCode;
            }
        });

        if (exitCode != 0) throw new InvalidOperationException("A configuracao terminou com codigo " + exitCode + ". Consulte o progresso acima.");
        SaveRoot(selectedRoot);
        Post(new Dictionary<string, object> { { "event", "install-complete" }, { "root", selectedRoot } });
        SendEnvironment();
    }

    private void PostLog(string line)
    {
        string[] parts = line.Split(new[] { '|' }, 5);
        if (parts.Length == 5 && parts[0] == "AICORTE_PROGRESS")
        {
            int progress;
            Int32.TryParse(parts[3], out progress);
            Post(new Dictionary<string, object> {
                { "event", "progress" }, { "id", parts[1] }, { "state", parts[2] },
                { "progress", progress }, { "message", parts[4] }
            });
        }
        else
            Post(new Dictionary<string, object> { { "event", "log" }, { "message", line } });
    }

    private async Task OpenDashboardAsync()
    {
        if (!IsInstallationReady(selectedRoot))
        {
            SendEnvironment();
            return;
        }
        SaveRoot(selectedRoot);
        StartDocker();
        StartBackend();
        for (int attempt = 0; attempt < 80; attempt++)
        {
            if (PortOpen(8787)) break;
            await Task.Delay(250);
        }
        if (!PortOpen(8787)) throw new InvalidOperationException("O painel nao respondeu. Consulte Principal\\logs\\principal.log.");
        browser.CoreWebView2.Navigate(DashboardUrl);
    }

    private void StartDocker()
    {
        if (DockerReady(selectedRoot)) return;
        Process.Start(new ProcessStartInfo {
            FileName = "wsl.exe",
            Arguments = "-d AICorteDocker -u root -- sleep infinity",
            UseShellExecute = false,
            CreateNoWindow = true,
            WindowStyle = ProcessWindowStyle.Hidden
        });
        for (int attempt = 0; attempt < 80 && !PortOpen(2375); attempt++) Thread.Sleep(250);
    }

    private void StartBackend()
    {
        if (PortOpen(8787)) return;
        string python = FindPython(selectedRoot);
        string principal = Path.Combine(selectedRoot, "Principal");
        string logFolder = Path.Combine(principal, "logs");
        Directory.CreateDirectory(logFolder);
        ProcessStartInfo start = new ProcessStartInfo {
            FileName = python,
            Arguments = "\"" + Path.Combine(principal, "app.py") + "\"",
            WorkingDirectory = principal,
            UseShellExecute = false,
            CreateNoWindow = true,
            WindowStyle = ProcessWindowStyle.Hidden,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8
        };
        start.EnvironmentVariables["PYTHONUTF8"] = "1";
        start.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
        start.EnvironmentVariables["AICORTE_ROOT"] = selectedRoot;
        string log = Path.Combine(logFolder, "principal.log");
        backendProcess = Process.Start(start);
        DataReceivedEventHandler append = delegate(object sender, DataReceivedEventArgs args)
        {
            if (args.Data == null) return;
            lock (logLock) File.AppendAllText(log, args.Data + Environment.NewLine, Encoding.UTF8);
        };
        backendProcess.OutputDataReceived += append;
        backendProcess.ErrorDataReceived += append;
        backendProcess.BeginOutputReadLine();
        backendProcess.BeginErrorReadLine();
    }

    private static string FindPython(string root)
    {
        if (String.IsNullOrWhiteSpace(root)) return null;
        string folder = Path.Combine(root, "app", "runtime", "uv-python");
        if (!Directory.Exists(folder)) return null;
        return Directory.GetDirectories(folder, "cpython-3.11-windows-*")
            .Select(path => Path.Combine(path, "python.exe"))
            .Where(File.Exists)
            .OrderByDescending(path => path)
            .FirstOrDefault();
    }

    private static bool DockerReady(string root)
    {
        if (String.IsNullOrWhiteSpace(root)) return false;
        string docker = Path.Combine(root, "app", "runtime", "docker", "docker.exe");
        if (!File.Exists(docker)) return false;
        try
        {
            ProcessStartInfo start = new ProcessStartInfo {
                FileName = docker, Arguments = "info --format {{.ServerVersion}}", UseShellExecute = false,
                CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true
            };
            start.EnvironmentVariables["DOCKER_HOST"] = "tcp://127.0.0.1:2375";
            using (Process process = Process.Start(start)) { return process.WaitForExit(3000) && process.ExitCode == 0; }
        }
        catch { return false; }
    }

    private static bool PortOpen(int port)
    {
        try
        {
            using (System.Net.Sockets.TcpClient client = new System.Net.Sockets.TcpClient())
            {
                IAsyncResult result = client.BeginConnect("127.0.0.1", port, null, null);
                return result.AsyncWaitHandle.WaitOne(300) && client.Connected;
            }
        }
        catch { return false; }
    }

    private bool IsInstallationReady(string root)
    {
        return !String.IsNullOrWhiteSpace(root)
            && File.Exists(Path.Combine(root, "Principal", "app.py"))
            && FindPython(root) != null;
    }

    private void CopyPayload(string targetRoot)
    {
        if (String.Equals(bundleRoot, targetRoot, StringComparison.OrdinalIgnoreCase)) return;
        string source = Path.Combine(bundleRoot, "Principal");
        string target = Path.Combine(targetRoot, "Principal");
        if (target.StartsWith(source + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException("A nova raiz nao pode ficar dentro da pasta Principal atual.");
        CopyDirectory(source, target);
        string targetExe = Path.Combine(targetRoot, "AICorte.exe");
        if (!File.Exists(targetExe)) File.Copy(Application.ExecutablePath, targetExe, true);
    }

    private static void CopyDirectory(string source, string target)
    {
        string[] skipped = { "state", "logs", "__pycache__", "banners", "tests" };
        Directory.CreateDirectory(target);
        foreach (string file in Directory.GetFiles(source))
        {
            if (Path.GetFileName(file).Equals(".installed", StringComparison.OrdinalIgnoreCase)) continue;
            File.Copy(file, Path.Combine(target, Path.GetFileName(file)), true);
        }
        foreach (string directory in Directory.GetDirectories(source))
        {
            string name = Path.GetFileName(directory);
            if (skipped.Contains(name, StringComparer.OrdinalIgnoreCase)) continue;
            CopyDirectory(directory, Path.Combine(target, name));
        }
    }

    private void HandleNewWindow(object sender, CoreWebView2NewWindowRequestedEventArgs args)
    {
        args.Handled = true;
        Uri uri;
        if (!Uri.TryCreate(args.Uri, UriKind.Absolute, out uri)) return;
        if (uri.Host != "127.0.0.1" && uri.Host != "localhost") { OpenExternal(args.Uri); return; }
        new ToolForm(args.Uri, selectedRoot).Show(this);
    }

    private static void OpenExternal(string url)
    {
        Uri uri;
        if (Uri.TryCreate(url, UriKind.Absolute, out uri) && (uri.Scheme == "https" || uri.Scheme == "http"))
            Process.Start(new ProcessStartInfo(uri.AbsoluteUri) { UseShellExecute = true });
    }

    private void Post(object payload)
    {
        if (browser.IsDisposed || browser.CoreWebView2 == null) return;
        string serialized = json.Serialize(payload);
        if (InvokeRequired) BeginInvoke(new Action(delegate { browser.CoreWebView2.PostWebMessageAsJson(serialized); }));
        else browser.CoreWebView2.PostWebMessageAsJson(serialized);
    }

    private static string ReadSavedRoot()
    {
        using (RegistryKey key = Registry.CurrentUser.OpenSubKey("Software\\AICorte"))
            return key == null ? null : Convert.ToString(key.GetValue("Root", ""));
    }

    private static void SaveRoot(string root)
    {
        using (RegistryKey key = Registry.CurrentUser.CreateSubKey("Software\\AICorte")) key.SetValue("Root", root);
    }

    private static bool InstallWebView2Runtime()
    {
        string installer = Path.Combine(Path.GetTempPath(), "AICorte-MicrosoftEdgeWebView2Setup.exe");
        try
        {
            using (WebClient client = new WebClient())
                client.DownloadFile("https://go.microsoft.com/fwlink/p/?LinkId=2124703", installer);
            using (Process process = Process.Start(new ProcessStartInfo {
                FileName = installer,
                Arguments = "/silent /install",
                UseShellExecute = true
            }))
            {
                process.WaitForExit();
                return process.ExitCode == 0;
            }
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message, "Falha ao instalar WebView2", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return false;
        }
    }
}

internal sealed class ToolForm : Form
{
    private readonly WebView2 browser = new WebView2();
    private readonly string url;
    private readonly string root;

    public ToolForm(string url, string root)
    {
        this.url = url;
        this.root = root;
        Text = "AICorte - Aplicativo";
        MinimumSize = new System.Drawing.Size(900, 640);
        WindowState = FormWindowState.Maximized;
        browser.Dock = DockStyle.Fill;
        Controls.Add(browser);
        Shown += async delegate
        {
            string profile = Path.Combine(root, "Principal", "state", "webview2-profile");
            CoreWebView2Environment environment = await CoreWebView2Environment.CreateAsync(null, profile);
            await browser.EnsureCoreWebView2Async(environment);
            browser.CoreWebView2.Settings.AreDevToolsEnabled = false;
            browser.CoreWebView2.Settings.IsStatusBarEnabled = false;
            browser.Source = new Uri(url);
        };
    }
}
