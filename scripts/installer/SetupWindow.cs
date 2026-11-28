using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Security.Cryptography;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Win32;

internal sealed class SetupWindow : Form
{
    private readonly Button install = new Button();
    private readonly Button theme = new Button();
    private readonly CheckBox shortcut = new CheckBox();
    private readonly CheckBox localModel = new CheckBox();
    private readonly ProgressBar progress = new ProgressBar();
    private readonly Label status = new Label();
    private bool dark;
    private bool busy;
    private bool installed;

    public SetupWindow()
    {
        Text = "Pivluma Setup";
        Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        ClientSize = new Size(560, 420);
        MinimumSize = new Size(576, 459);
        MaximizeBox = false;
        StartPosition = FormStartPosition.CenterScreen;
        AutoScaleMode = AutoScaleMode.Dpi;
        Font = new Font("Segoe UI", 10);
        FormClosing += delegate(object sender, FormClosingEventArgs e) { if (busy) e.Cancel = true; };

        var content = new TableLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(25), ColumnCount = 2 };
        content.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        content.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        Controls.Add(content);

        var heading = new Label { Text = "Pivluma", Font = new Font("Segoe UI", 24, FontStyle.Bold), AutoSize = true };
        content.Controls.Add(heading, 0, 0);
        theme.AutoSize = true;
        theme.Click += delegate { ApplyTheme(!dark); };
        content.Controls.Add(theme, 1, 0);
        AddRow(content, new Label {
            Text = "Windows x64 setup",
            AutoSize = true, Margin = new Padding(0, 4, 0, 20)
        }, 1);
        AddRow(content, new Label {
            Text = "Install Pivluma in Program Files. Windows will ask for administrator approval.",
            AutoSize = true, MaximumSize = new Size(475, 0)
        }, 2);

        shortcut.Text = "Create a desktop shortcut";
        shortcut.Checked = true;
        shortcut.AutoSize = true;
        shortcut.Margin = new Padding(0, 14, 0, 12);
        AddRow(content, shortcut, 3);

        localModel.Text = "Download the local AI model (1.31 GB, optional)";
        localModel.AutoSize = true;
        AddRow(content, localModel, 4);

        progress.Dock = DockStyle.Top;
        progress.Style = ProgressBarStyle.Marquee;
        progress.MarqueeAnimationSpeed = 0;
        progress.Height = 8;
        progress.Visible = false;
        AddRow(content, progress, 5);

        status.AutoSize = true;
        status.MaximumSize = new Size(475, 0);
        status.Margin = new Padding(0, 10, 0, 12);
        AddRow(content, status, 6);

        install.Text = "Install";
        install.AutoSize = true;
        install.Anchor = AnchorStyles.Bottom | AnchorStyles.Right;
        install.Click += async delegate { if (installed) Close(); else await Install(); };
        content.Controls.Add(install, 1, 7);
        AcceptButton = install;

        var setting = Registry.GetValue(
            @"HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            "AppsUseLightTheme", 1
        );
        ApplyTheme(setting is int && (int)setting == 0);
    }

    private static void AddRow(TableLayoutPanel content, Control control, int row)
    {
        content.Controls.Add(control, 0, row);
        content.SetColumnSpan(control, 2);
    }

    private void ApplyTheme(bool useDark)
    {
        dark = useDark;
        Color background = useDark ? Color.FromArgb(17, 27, 41) : Color.FromArgb(244, 249, 251);
        Color text = useDark ? Color.FromArgb(235, 243, 252) : Color.FromArgb(25, 43, 60);
        BackColor = background;
        ForeColor = text;
        theme.Text = useDark ? "Light theme" : "Dark theme";
        theme.FlatStyle = FlatStyle.Flat;
        theme.BackColor = useDark ? Color.FromArgb(38, 56, 76) : Color.White;
        theme.ForeColor = text;
        install.FlatStyle = FlatStyle.Flat;
        install.BackColor = useDark ? Color.FromArgb(45, 124, 114) : Color.FromArgb(0, 106, 96);
        install.ForeColor = Color.White;
    }

    private async Task Install()
    {
        if (busy) return;
        bool desktop = shortcut.Checked;
        bool downloadModel = localModel.Checked;
        busy = true;
        shortcut.Enabled = false;
        localModel.Enabled = false;
        install.Enabled = false;
        status.Text = "Waiting for Windows approval or installing Pivlumaâ€¦";
        progress.Visible = true;
        progress.MarqueeAnimationSpeed = 35;
        try
        {
            int result = await Task.Run(() => RunInstaller(desktop));
            if (result == 0 || result == 3010)
            {
                status.Text = result == 3010 ? "Installed. Windows requests a restart." :
                    "Pivluma is installed. Open it from the Start menu.";
                if (result == 0 && downloadModel)
                {
                    status.Text = "Pivluma is installed. Downloading the optional model…";
                    int downloaded = await Task.Run(() => RunModelDownload());
                    status.Text = downloaded == 0 ? "Pivluma and its local model are ready." :
                        "Pivluma is installed. You can retry the model download from Settings.";
                }
                install.Text = "Close";
                install.Enabled = true;
                installed = true;
                return;
            }
            status.Text = "Windows Installer returned code " + result + ".";
        }
        catch (Win32Exception ex)
        {
            status.Text = ex.NativeErrorCode == 1223 ? "Installation approval was cancelled." :
                "Windows could not start setup. Error " + ex.NativeErrorCode + ".";
        }
        catch (IOException)
        {
            status.Text = "The setup package could not be written. Check available disk space.";
        }
        finally
        {
            busy = false;
            progress.MarqueeAnimationSpeed = 0;
            progress.Visible = false;
            shortcut.Enabled = true;
            localModel.Enabled = true;
            install.Enabled = true;
        }
    }

    private static int RunModelDownload()
    {
        try
        {
            string programFiles = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles);
            string executable = Path.Combine(programFiles, "Pivluma", "Pivluma.exe");
            using (var process = Process.Start(new ProcessStartInfo {
                FileName = executable, Arguments = "--download-local-model", UseShellExecute = false
            }))
            {
                process.WaitForExit();
                return process.ExitCode;
            }
        }
        catch (Win32Exception) { return 1; }
        catch (IOException) { return 1; }
    }

    private static int RunInstaller(bool desktop)
    {
        string directory = Path.Combine(Path.GetTempPath(), "pivluma-setup-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
        string package = Path.Combine(directory, "Pivluma.msi");
        try
        {
            using (Stream source = Package())
            using (var output = new FileStream(package, FileMode.CreateNew))
                source.CopyTo(output);
            var start = new ProcessStartInfo {
                FileName = Path.Combine(Environment.SystemDirectory, "msiexec.exe"),
                Arguments = "/i \"" + package + "\" /qn /norestart DESKTOPSHORTCUT=" + (desktop ? "1" : "0"),
                UseShellExecute = true,
                Verb = "runas"
            };
            using (var process = Process.Start(start))
            {
                process.WaitForExit();
                return process.ExitCode;
            }
        }
        finally
        {
            try { File.Delete(package); Directory.Delete(directory); }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
        }
    }

    private static Stream Package()
    {
        return Assembly.GetExecutingAssembly().GetManifestResourceStream("Pivluma.msi");
    }

    [STAThread]
    private static int Main(string[] args)
    {
        if (args.Length == 2 && args[0] == "--verify-package")
        {
            using (Stream package = Package())
            using (SHA256 hash = SHA256.Create())
            {
                string digest = BitConverter.ToString(hash.ComputeHash(package)).Replace("-", "");
                return String.Equals(digest, args[1], StringComparison.OrdinalIgnoreCase) ? 0 : 1;
            }
        }
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        Application.Run(new SetupWindow());
        return 0;
    }
}
