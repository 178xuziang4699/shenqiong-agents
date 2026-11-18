"""生成 App 启动图标（无需外部设计稿，纯绘制）。

紫蓝渐变圆角方块 + 白色机器人/对话气泡，匹配平台浅色 SaaS 风格。
输出到 res/drawable-{m,h,xh,xxh,xxxh}dpi/ic_launcher.png。
"""
from pathlib import Path
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
DENSITIES = {
    "mdpi": 48, "hdpi": 72, "xhdpi": 96,
    "xxhdpi": 144, "xxxhdpi": 192,
}


def rounded(size: int) -> Image.Image:
    s = size * 4  # 超采样抗锯齿
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # 紫蓝竖向渐变
    for y in range(s):
        t = y / s
        r = int(89 + (124 - 89) * t)
        g = int(37 + (88 - 37) * t)
        b = int(220 + (247 - 220) * t)
        d.line([(0, y), (s, y)], fill=(r, g, b, 255))
    # 圆角遮罩
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, s - 1, s - 1], radius=int(s * 0.23), fill=255)
    img.putalpha(mask)
    d = ImageDraw.Draw(img)

    # 对话气泡（白色）
    bx0, by0 = int(s * 0.24), int(s * 0.26)
    bx1, by1 = int(s * 0.76), int(s * 0.62)
    d.rounded_rectangle([bx0, by0, bx1, by1], radius=int(s * 0.11),
                        fill=(255, 255, 255, 240))
    # 气泡小尾巴
    d.polygon([(int(s * 0.34), by1 - 2), (int(s * 0.30), int(s * 0.72)),
               (int(s * 0.47), by1 - 2)], fill=(255, 255, 255, 240))
    # 三个点
    cy = int((by0 + by1) / 2)
    for cx in (0.40, 0.50, 0.60):
        x = int(s * cx)
        rr = int(s * 0.022)
        d.ellipse([x - rr, cy - rr, x + rr, cy + rr], fill=(89, 37, 220, 255))

    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    for dpi, size in DENSITIES.items():
        out = HERE / "res" / f"drawable-{dpi}"
        out.mkdir(parents=True, exist_ok=True)
        p = out / "ic_launcher.png"
        rounded(size).save(p, "PNG")
        print(f"  {p.relative_to(HERE)}  {size}x{size}")
    # 应用商店/桌面通用大图
    (HERE / "ic_launcher_512.png").__class__  # noqa
    rounded(512).save(HERE / "ic_launcher_512.png", "PNG")
    print("  ic_launcher_512.png  512x512")


if __name__ == "__main__":
    main()
