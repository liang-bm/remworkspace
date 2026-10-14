param(
    [string]$ProjectRoot = (Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)))
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing
Add-Type -ReferencedAssemblies "System.Drawing" -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.IO;

public static class RemPetRenderFrames
{
    private static bool IsVisible(Color color)
    {
        return color.A >= 8;
    }

    private static bool IsDarkOutline(Color color)
    {
        int maximum = Math.Max(color.R, Math.Max(color.G, color.B));
        int minimum = Math.Min(color.R, Math.Min(color.G, color.B));
        return maximum <= 92 && maximum - minimum <= 72;
    }

    private static bool IsMagentaSpill(Color color)
    {
        return color.R >= 105
            && color.B >= 90
            && color.G <= 105
            && color.R - color.G >= 35
            && color.B - color.G >= 22;
    }

    private static bool TouchesTransparency(Color[,] pixels, int x, int y)
    {
        int width = pixels.GetLength(0);
        int height = pixels.GetLength(1);
        for (int offsetY = -1; offsetY <= 1; offsetY++)
        {
            for (int offsetX = -1; offsetX <= 1; offsetX++)
            {
                if (offsetX == 0 && offsetY == 0)
                {
                    continue;
                }
                int neighborX = x + offsetX;
                int neighborY = y + offsetY;
                if (neighborX < 0 || neighborX >= width || neighborY < 0 || neighborY >= height)
                {
                    return true;
                }
                if (!IsVisible(pixels[neighborX, neighborY]))
                {
                    return true;
                }
            }
        }
        return false;
    }

    private static Color[,] ReadPixels(Bitmap source)
    {
        Color[,] pixels = new Color[source.Width, source.Height];
        for (int y = 0; y < source.Height; y++)
        {
            for (int x = 0; x < source.Width; x++)
            {
                pixels[x, y] = source.GetPixel(x, y);
            }
        }
        return pixels;
    }

    private static Bitmap CleanOuterEdge(Bitmap source)
    {
        Color[,] pixels = ReadPixels(source);
        int width = source.Width;
        int height = source.Height;

        // Only soften the exterior ink color. Deleting outline pixels creates
        // a ragged silhouette, while changing alpha creates a grey halo on
        // light desktops. Interior facial and clothing line art is untouched.
        List<Point> soften = new List<Point>();
        for (int y = 0; y < height; y++)
        {
            for (int x = 0; x < width; x++)
            {
                Color pixel = pixels[x, y];
                if (IsVisible(pixel)
                    && TouchesTransparency(pixels, x, y)
                    && IsDarkOutline(pixel))
                {
                    soften.Add(new Point(x, y));
                }
            }
        }
        foreach (Point point in soften)
        {
            Color pixel = pixels[point.X, point.Y];
            pixels[point.X, point.Y] = Color.FromArgb(
                pixel.A,
                78,
                90,
                104);
        }

        Bitmap cleaned = new Bitmap(width, height, PixelFormat.Format32bppArgb);
        for (int y = 0; y < height; y++)
        {
            for (int x = 0; x < width; x++)
            {
                Color pixel = pixels[x, y];
                cleaned.SetPixel(
                    x,
                    y,
                    pixel.A < 8
                        ? Color.Transparent
                        : Color.FromArgb(pixel.A, pixel.R, pixel.G, pixel.B));
            }
        }
        return cleaned;
    }

    private static Bitmap Resize(Bitmap source, int width, int height)
    {
        Bitmap output = new Bitmap(width, height, PixelFormat.Format32bppArgb);
        using (Graphics graphics = Graphics.FromImage(output))
        {
            graphics.Clear(Color.Transparent);
            graphics.CompositingMode = CompositingMode.SourceCopy;
            graphics.CompositingQuality = CompositingQuality.HighQuality;
            graphics.InterpolationMode = InterpolationMode.HighQualityBicubic;
            graphics.PixelOffsetMode = PixelOffsetMode.HighQuality;
            graphics.SmoothingMode = SmoothingMode.HighQuality;
            graphics.DrawImage(
                source,
                new Rectangle(0, 0, width, height),
                new Rectangle(0, 0, source.Width, source.Height),
                GraphicsUnit.Pixel);
        }
        return output;
    }

    public static void RenderFile(
        string sourcePath,
        string outputPath,
        int width,
        int height)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(outputPath));
        using (Bitmap source = new Bitmap(sourcePath))
        using (Bitmap cleaned = CleanOuterEdge(source))
        using (Bitmap rendered = Resize(cleaned, width, height))
        {
            rendered.Save(outputPath, ImageFormat.Png);
        }
    }

    public static void MakeComparison(
        string sourcePath,
        string renderedPath,
        string outputPath)
    {
        using (Bitmap source = new Bitmap(sourcePath))
        using (Bitmap rendered = new Bitmap(renderedPath))
        using (Bitmap sourceLarge = Resize(source, 288, 312))
        using (Bitmap sheet = new Bitmap(640, 360, PixelFormat.Format32bppArgb))
        using (Graphics graphics = Graphics.FromImage(sheet))
        {
            graphics.Clear(Color.White);
            graphics.DrawImageUnscaled(sourceLarge, 16, 24);
            graphics.DrawImageUnscaled(rendered, 336, 24);
            using (Font font = new Font("Segoe UI", 13, FontStyle.Bold))
            using (Brush brush = new SolidBrush(Color.FromArgb(35, 52, 68)))
            {
                graphics.DrawString("Original scaling", font, brush, 88, 330);
                graphics.DrawString("Smooth scaling + softer ink", font, brush, 350, 330);
            }
            Directory.CreateDirectory(Path.GetDirectoryName(outputPath));
            sheet.Save(outputPath, ImageFormat.Png);
        }
    }

    public static void MakeContactSheet(string[] framePaths, string outputPath)
    {
        const int columns = 5;
        const int cellWidth = 288;
        const int cellHeight = 312;
        int rows = (int)Math.Ceiling(framePaths.Length / (double)columns);
        using (Bitmap sheet = new Bitmap(
            columns * cellWidth,
            rows * cellHeight,
            PixelFormat.Format32bppArgb))
        using (Graphics graphics = Graphics.FromImage(sheet))
        {
            graphics.Clear(Color.White);
            for (int index = 0; index < framePaths.Length; index++)
            {
                int x = (index % columns) * cellWidth;
                int y = (index / columns) * cellHeight;
                if ((index + index / columns) % 2 == 1)
                {
                    using (Brush background = new SolidBrush(Color.FromArgb(236, 241, 245)))
                    {
                        graphics.FillRectangle(background, x, y, cellWidth, cellHeight);
                    }
                }
                using (Bitmap frame = new Bitmap(framePaths[index]))
                {
                    graphics.DrawImageUnscaled(frame, x, y);
                }
            }
            Directory.CreateDirectory(Path.GetDirectoryName(outputPath));
            sheet.Save(outputPath, ImageFormat.Png);
        }
    }
}
'@

$AssetRoot = Join-Path $ProjectRoot "app\remcompanion\assets\pet"
$RenderedRoot = Join-Path $AssetRoot "rendered"
$Scales = @(
    @{ Name = "075"; Width = 144; Height = 156 },
    @{ Name = "100"; Width = 192; Height = 208 },
    @{ Name = "125"; Width = 240; Height = 260 },
    @{ Name = "150"; Width = 288; Height = 312 }
)

$Sources = @()
$Sources += Get-ChildItem -LiteralPath (Join-Path $AssetRoot "idle") -Filter "idle_*.png"
Get-ChildItem -LiteralPath (Join-Path $AssetRoot "actions") -Directory |
    ForEach-Object {
        $ActionName = $_.Name
        $Sources += Get-ChildItem -LiteralPath $_.FullName -Filter "$($ActionName)_*.png"
    }

foreach ($Source in $Sources) {
    $State = if ($Source.Directory.Name -eq "idle") {
        "idle"
    } else {
        $Source.Directory.Name
    }
    foreach ($Scale in $Scales) {
        $Output = Join-Path (
            Join-Path (Join-Path $RenderedRoot $Scale.Name) $State
        ) $Source.Name
        [RemPetRenderFrames]::RenderFile(
            $Source.FullName,
            $Output,
            $Scale.Width,
            $Scale.Height
        )
    }
}

$Comparison = Join-Path $ProjectRoot "artifacts\pet-edge-comparison.png"
[RemPetRenderFrames]::MakeComparison(
    (Join-Path $AssetRoot "idle\idle_00.png"),
    (Join-Path $RenderedRoot "150\idle\idle_00.png"),
    $Comparison
)
$ContactSheet = Join-Path $ProjectRoot "artifacts\pet-render-contact.png"
$ContactFrames = Get-ChildItem -LiteralPath (Join-Path $RenderedRoot "150") -Directory |
    Sort-Object Name |
    ForEach-Object {
        Get-ChildItem -LiteralPath $_.FullName -Filter "*.png" |
            Sort-Object Name |
            Select-Object -First 1
    }
[RemPetRenderFrames]::MakeContactSheet(
    @($ContactFrames.FullName),
    $ContactSheet
)

Write-Output "source_frames=$($Sources.Count)"
Write-Output "rendered_frames=$($Sources.Count * $Scales.Count)"
Write-Output "comparison=$Comparison"
Write-Output "contact_sheet=$ContactSheet"
