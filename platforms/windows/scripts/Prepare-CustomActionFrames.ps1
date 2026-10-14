param(
    [string]$ProjectRoot = (Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)))
)

$ErrorActionPreference = "Stop"

# 用户提供的两张图是 5x2 黑底合图。裁剪时保留完整单元格中的人物与动作
# 配图，只排除底部编号和从画布边缘连通的黑色背景，再统一装入 192x208
# 透明帧。这样不会误删蒸汽、怒气符号、动作线等有意的小配图。
Add-Type -AssemblyName System.Drawing
Add-Type -ReferencedAssemblies "System.Drawing" -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.IO;

public static class RemActionFrameExtractor
{
    private const int Columns = 5;
    private const int Rows = 2;
    private const int OutputWidth = 192;
    private const int OutputHeight = 208;

    private static bool IsBackground(Color color)
    {
        int maximum = Math.Max(color.R, Math.Max(color.G, color.B));
        int minimum = Math.Min(color.R, Math.Min(color.G, color.B));
        return maximum <= 24 && maximum - minimum <= 14;
    }

    private static void QueueIfBackground(
        Bitmap source,
        Rectangle cell,
        bool[] background,
        Queue<Point> queue,
        int x,
        int y)
    {
        int localX = x - cell.Left;
        int localY = y - cell.Top;
        int index = localY * cell.Width + localX;
        if (background[index] || !IsBackground(source.GetPixel(x, y)))
        {
            return;
        }
        background[index] = true;
        queue.Enqueue(new Point(x, y));
    }

    private static bool[] FloodBackground(Bitmap source, Rectangle cell)
    {
        bool[] background = new bool[cell.Width * cell.Height];
        Queue<Point> queue = new Queue<Point>();
        for (int x = cell.Left; x < cell.Right; x++)
        {
            QueueIfBackground(source, cell, background, queue, x, cell.Top);
            QueueIfBackground(source, cell, background, queue, x, cell.Bottom - 1);
        }
        for (int y = cell.Top; y < cell.Bottom; y++)
        {
            QueueIfBackground(source, cell, background, queue, cell.Left, y);
            QueueIfBackground(source, cell, background, queue, cell.Right - 1, y);
        }

        int[] dx = { -1, 1, 0, 0 };
        int[] dy = { 0, 0, -1, 1 };
        while (queue.Count > 0)
        {
            Point point = queue.Dequeue();
            for (int direction = 0; direction < 4; direction++)
            {
                int x = point.X + dx[direction];
                int y = point.Y + dy[direction];
                if (x < cell.Left || x >= cell.Right || y < cell.Top || y >= cell.Bottom)
                {
                    continue;
                }
                QueueIfBackground(source, cell, background, queue, x, y);
            }
        }
        return background;
    }

    private static HashSet<int> RelevantForeground(
        Bitmap source,
        Rectangle cell,
        bool[] background,
        int contentTop,
        int contentBottom)
    {
        bool[] visited = new bool[background.Length];
        List<HashSet<int>> components = new List<HashSet<int>>();
        int[] dx = { -1, 1, 0, 0 };
        int[] dy = { 0, 0, -1, 1 };

        for (int localY = contentTop; localY <= contentBottom; localY++)
        {
            for (int localX = 0; localX < cell.Width; localX++)
            {
                int start = localY * cell.Width + localX;
                if (background[start] || visited[start])
                {
                    continue;
                }

                Queue<Point> queue = new Queue<Point>();
                HashSet<int> component = new HashSet<int>();
                queue.Enqueue(new Point(localX, localY));
                visited[start] = true;
                while (queue.Count > 0)
                {
                    Point point = queue.Dequeue();
                    int index = point.Y * cell.Width + point.X;
                    component.Add(index);
                    for (int direction = 0; direction < 4; direction++)
                    {
                        int x = point.X + dx[direction];
                        int y = point.Y + dy[direction];
                        if (x < 0 || x >= cell.Width || y < contentTop || y > contentBottom)
                        {
                            continue;
                        }
                        int neighbor = y * cell.Width + x;
                        if (background[neighbor] || visited[neighbor])
                        {
                            continue;
                        }
                        visited[neighbor] = true;
                        queue.Enqueue(new Point(x, y));
                    }
                }
                if (component.Count >= 4)
                {
                    components.Add(component);
                }
            }
        }

        if (components.Count == 0)
        {
            throw new InvalidDataException("No foreground sprite was found in one action cell.");
        }
        int largestIndex = 0;
        for (int index = 1; index < components.Count; index++)
        {
            if (components[index].Count > components[largestIndex].Count)
            {
                largestIndex = index;
            }
        }

        Rectangle mainBounds = ComponentBounds(components[largestIndex], cell.Width);
        HashSet<int> selected = new HashSet<int>(components[largestIndex]);
        for (int index = 0; index < components.Count; index++)
        {
            if (index == largestIndex)
            {
                continue;
            }
            HashSet<int> component = components[index];
            Rectangle bounds = ComponentBounds(component, cell.Width);
            bool touchesSide = bounds.Left <= 1 || bounds.Right >= cell.Width - 2;
            int horizontalGap = Math.Max(
                0,
                Math.Max(mainBounds.Left - bounds.Right, bounds.Left - mainBounds.Right));
            int verticalGap = Math.Max(
                0,
                Math.Max(mainBounds.Top - bounds.Bottom, bounds.Top - mainBounds.Bottom));
            double distance = Math.Sqrt(
                horizontalGap * horizontalGap + verticalGap * verticalGap);

            // Preserve intentional nearby symbols, puffs and motion marks.
            // Reject tiny edge fragments caused by a neighboring atlas cell.
            bool compactEdgeEffect = touchesSide
                && component.Count >= 120
                && bounds.Width >= 12
                && bounds.Height >= 12
                && bounds.Width * 2 >= bounds.Height
                && bounds.Height * 2 >= bounds.Width
                && AverageBrightness(component, source, cell) >= 185
                && distance <= 80;
            if ((!touchesSide
                    && component.Count >= 8
                    && (distance <= 72 || component.Count >= 420))
                || compactEdgeEffect)
            {
                selected.UnionWith(component);
            }
        }
        return selected;
    }

    private static double AverageBrightness(
        HashSet<int> component,
        Bitmap source,
        Rectangle cell)
    {
        double total = 0;
        foreach (int index in component)
        {
            int x = index % cell.Width;
            int y = index / cell.Width;
            Color color = source.GetPixel(cell.Left + x, cell.Top + y);
            total += (color.R + color.G + color.B) / 3.0;
        }
        return total / component.Count;
    }

    private static Rectangle ComponentBounds(HashSet<int> component, int width)
    {
        int left = width;
        int top = Int32.MaxValue;
        int right = 0;
        int bottom = 0;
        foreach (int index in component)
        {
            int x = index % width;
            int y = index / width;
            left = Math.Min(left, x);
            top = Math.Min(top, y);
            right = Math.Max(right, x);
            bottom = Math.Max(bottom, y);
        }
        return Rectangle.FromLTRB(left, top, right + 1, bottom + 1);
    }

    private static Bitmap ExtractCell(Bitmap source, Rectangle cell, int row)
    {
        bool[] background = FloodBackground(source, cell);
        // Every slot uses the same crop. The lower 10.5% only contains the
        // printed frame number, while intentional effects remain above it.
        int left = 0;
        int top = row == 0
            ? (int)Math.Round(cell.Height * 0.045)
            : 0;
        int right = cell.Width - 1;
        int bottom = Math.Min(
            cell.Height - 1,
            (int)Math.Floor(cell.Height * (row == 0 ? 0.89 : 0.81)));
        int cropWidth = right - left + 1;
        int cropHeight = bottom - top + 1;
        HashSet<int> foreground = RelevantForeground(
            source,
            cell,
            background,
            top,
            bottom);

        using (Bitmap isolated = new Bitmap(cropWidth, cropHeight, PixelFormat.Format32bppArgb))
        {
            for (int y = top; y <= bottom; y++)
            {
                for (int x = left; x <= right; x++)
                {
                    int index = y * cell.Width + x;
                    if (!foreground.Contains(index))
                    {
                        isolated.SetPixel(x - left, y - top, Color.Transparent);
                        continue;
                    }
                    Color pixel = source.GetPixel(cell.Left + x, cell.Top + y);
                    isolated.SetPixel(
                        x - left,
                        y - top,
                        Color.FromArgb(255, pixel.R, pixel.G, pixel.B));
                }
            }

            double scale = Math.Min(180.0 / cropWidth, 202.0 / cropHeight);
            int width = Math.Max(1, (int)Math.Round(cropWidth * scale));
            int height = Math.Max(1, (int)Math.Round(cropHeight * scale));
            int destinationX = (OutputWidth - width) / 2;
            int destinationY = (OutputHeight - height) / 2;
            Bitmap output = new Bitmap(OutputWidth, OutputHeight, PixelFormat.Format32bppArgb);
            using (Graphics graphics = Graphics.FromImage(output))
            {
                graphics.Clear(Color.Transparent);
                graphics.CompositingMode = CompositingMode.SourceCopy;
                graphics.CompositingQuality = CompositingQuality.HighQuality;
                graphics.InterpolationMode = InterpolationMode.HighQualityBicubic;
                graphics.PixelOffsetMode = PixelOffsetMode.HighQuality;
                graphics.SmoothingMode = SmoothingMode.HighQuality;
                graphics.DrawImage(
                    isolated,
                    new Rectangle(destinationX, destinationY, width, height),
                    new Rectangle(0, 0, cropWidth, cropHeight),
                    GraphicsUnit.Pixel);
            }
            return output;
        }
    }

    public static string[] ProcessSheet(string sourcePath, string outputDirectory, string action)
    {
        Directory.CreateDirectory(outputDirectory);
        List<string> outputs = new List<string>();
        using (Bitmap source = new Bitmap(sourcePath))
        {
            for (int row = 0; row < Rows; row++)
            {
                for (int column = 0; column < Columns; column++)
                {
                    int left = (int)Math.Round(column * source.Width / (double)Columns);
                    int right = (int)Math.Round((column + 1) * source.Width / (double)Columns);
                    int top = (int)Math.Round(row * source.Height / (double)Rows);
                    int bottom = (int)Math.Round((row + 1) * source.Height / (double)Rows);
                    int frameIndex = row * Columns + column;
                    // The second-row first angry pose paints its right steam
                    // puff into the inter-cell gap. Shift the fixed-width crop
                    // right so both intentional puffs stay in the same frame.
                    if (action == "little-angry" && frameIndex == 5)
                    {
                        int shift = Math.Min(36, source.Width - right);
                        left += shift;
                        right += shift;
                    }
                    Rectangle cell = Rectangle.FromLTRB(left, top, right, bottom);
                    string output = Path.Combine(
                        outputDirectory,
                        String.Format("{0}_{1:D2}.png", action, frameIndex));
                    using (Bitmap frame = ExtractCell(source, cell, row))
                    {
                        frame.Save(output, ImageFormat.Png);
                    }
                    outputs.Add(output);
                }
            }
        }
        return outputs.ToArray();
    }

    public static string[] CopyFrames(string[] framePaths, string outputDirectory)
    {
        Directory.CreateDirectory(outputDirectory);
        List<string> outputs = new List<string>();
        foreach (string framePath in framePaths)
        {
            string output = Path.Combine(
                outputDirectory,
                Path.GetFileName(framePath));
            File.Copy(framePath, output, true);
            outputs.Add(output);
        }
        return outputs.ToArray();
    }

    public static void ReverseFramePassInPlace(string[] framePaths, int startIndex)
    {
        // The source sheet contains two five-frame passes that both travel
        // from screen-right to screen-left. A pixel mirror would swap Rem's
        // covered eye and hair ornament, causing an obvious identity pop.
        // Reverse the second pass in time instead. It becomes the return trip
        // while the asymmetric face, hair and accessories stay canonical.
        List<byte[]> pass = new List<byte[]>();
        for (int index = startIndex; index < framePaths.Length; index++)
        {
            pass.Add(File.ReadAllBytes(framePaths[index]));
        }
        pass.Reverse();
        for (int offset = 0; offset < pass.Count; offset++)
        {
            File.WriteAllBytes(framePaths[startIndex + offset], pass[offset]);
        }
    }

    public static void MakeContactSheet(string[] framePaths, string outputPath)
    {
        int columns = 10;
        int rows = (int)Math.Ceiling(framePaths.Length / (double)columns);
        using (Bitmap sheet = new Bitmap(
            columns * OutputWidth,
            rows * OutputHeight,
            PixelFormat.Format32bppArgb))
        using (Graphics graphics = Graphics.FromImage(sheet))
        {
            graphics.Clear(Color.FromArgb(255, 28, 36, 47));
            for (int index = 0; index < framePaths.Length; index++)
            {
                using (Bitmap frame = new Bitmap(framePaths[index]))
                {
                    graphics.DrawImageUnscaled(
                        frame,
                        (index % columns) * OutputWidth,
                        (index / columns) * OutputHeight);
                }
            }
            Directory.CreateDirectory(Path.GetDirectoryName(outputPath));
            sheet.Save(outputPath, ImageFormat.Png);
        }
    }
}
'@

$SourceRoot = Join-Path $ProjectRoot "resources\source-art\pet-actions"
if (-not (Test-Path -LiteralPath $SourceRoot -PathType Container)) {
    $SourceRoot = Join-Path $ProjectRoot "assets-source\pet-actions"
}
$ActionRoot = Join-Path $ProjectRoot "app\remcompanion\assets\pet\actions"
$Artifacts = Join-Path $ProjectRoot "artifacts"

$HappyFrames = [RemActionFrameExtractor]::ProcessSheet(
    (Join-Path $SourceRoot "happy.png"),
    (Join-Path $ActionRoot "happy"),
    "happy"
)
$HappyOriginalFrames = [RemActionFrameExtractor]::CopyFrames(
    $HappyFrames,
    (Join-Path $Artifacts "happy-original")
)
[RemActionFrameExtractor]::ReverseFramePassInPlace($HappyFrames, 5)
$AngryFrames = [RemActionFrameExtractor]::ProcessSheet(
    (Join-Path $SourceRoot "little-angry.png"),
    (Join-Path $ActionRoot "little-angry"),
    "little-angry"
)
[RemActionFrameExtractor]::MakeContactSheet(
    @($HappyFrames + $AngryFrames),
    (Join-Path $Artifacts "custom-actions-contact.png")
)
[RemActionFrameExtractor]::MakeContactSheet(
    @($HappyOriginalFrames + $HappyFrames),
    (Join-Path $Artifacts "happy-symmetry-contact.png")
)

Write-Output "happy_frames=$($HappyFrames.Count)"
Write-Output "little_angry_frames=$($AngryFrames.Count)"
Write-Output "contact_sheet=$(Join-Path $Artifacts 'custom-actions-contact.png')"
Write-Output "happy_comparison=$(Join-Path $Artifacts 'happy-symmetry-contact.png')"
