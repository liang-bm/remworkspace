param(
    [Parameter(Mandatory = $true)]
    [int]$ProcessId,
    [string]$OutputDirectory = ""
)

$ErrorActionPreference = "Stop"
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path (Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))) "artifacts\window-captures"
}
Add-Type -AssemblyName System.Drawing
Add-Type -ReferencedAssemblies "System.Drawing" -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

public static class ProcessWindowCapture
{
    private delegate bool EnumWindowsProc(IntPtr hwnd, IntPtr parameter);

    [StructLayout(LayoutKind.Sequential)]
    private struct Rect
    {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }

    [DllImport("user32.dll")]
    private static extern bool EnumWindows(EnumWindowsProc callback, IntPtr parameter);

    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint processId);

    [DllImport("user32.dll")]
    private static extern bool IsWindowVisible(IntPtr hwnd);

    [DllImport("user32.dll")]
    private static extern bool GetWindowRect(IntPtr hwnd, out Rect rect);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetWindowText(IntPtr hwnd, StringBuilder text, int capacity);

    [DllImport("user32.dll")]
    private static extern bool PrintWindow(IntPtr hwnd, IntPtr hdc, uint flags);

    [DllImport("user32.dll")]
    private static extern uint GetDpiForWindow(IntPtr hwnd);

    public static string[] Capture(uint targetProcessId, string outputDirectory)
    {
        Directory.CreateDirectory(outputDirectory);
        List<string> outputs = new List<string>();
        int index = 0;
        EnumWindows(delegate(IntPtr hwnd, IntPtr parameter)
        {
            uint processId;
            GetWindowThreadProcessId(hwnd, out processId);
            if (processId != targetProcessId || !IsWindowVisible(hwnd))
            {
                return true;
            }

            Rect rect;
            if (!GetWindowRect(hwnd, out rect))
            {
                return true;
            }
            int width = rect.Right - rect.Left;
            int height = rect.Bottom - rect.Top;
            uint dpi = GetDpiForWindow(hwnd);
            if (dpi > 96)
            {
                width = (int)Math.Round(width * dpi / 96.0);
                height = (int)Math.Round(height * dpi / 96.0);
            }
            if (width < 2 || height < 2)
            {
                return true;
            }

            StringBuilder title = new StringBuilder(256);
            GetWindowText(hwnd, title, title.Capacity);
            string safeTitle = title.ToString();
            foreach (char invalid in System.IO.Path.GetInvalidFileNameChars())
            {
                safeTitle = safeTitle.Replace(invalid, '_');
            }
            if (String.IsNullOrWhiteSpace(safeTitle))
            {
                safeTitle = "untitled";
            }

            string output = System.IO.Path.Combine(
                outputDirectory,
                String.Format("{0:D2}-{1}-{2}x{3}.png", index, safeTitle, width, height));
            using (Bitmap bitmap = new Bitmap(width, height, PixelFormat.Format32bppArgb))
            using (Graphics graphics = Graphics.FromImage(bitmap))
            {
                IntPtr hdc = graphics.GetHdc();
                try
                {
                    // PW_RENDERFULLCONTENT captures only this application window.
                    PrintWindow(hwnd, hdc, 2);
                }
                finally
                {
                    graphics.ReleaseHdc(hdc);
                }
                bitmap.Save(output, ImageFormat.Png);
            }
            outputs.Add(output);
            index++;
            return true;
        }, IntPtr.Zero);
        return outputs.ToArray();
    }
}
'@

[ProcessWindowCapture]::Capture(
    [uint32]$ProcessId,
    (New-Item -ItemType Directory -Force -Path $OutputDirectory).FullName
)
