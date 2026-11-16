import glob, sys
from PIL import Image, ImageDraw, ImageFont
files = sorted(glob.glob(sys.argv[1] + '/*.ppm'))
cols = 5; s = 240; pad = 16; lab = 22
rows = (len(files) + cols - 1) // cols
sheet = Image.new('RGB', (cols * (s + pad) + pad, rows * (s + pad + lab) + pad), (235, 235, 235))
mask = Image.new('L', (s, s), 0); ImageDraw.Draw(mask).ellipse((0, 0, s - 1, s - 1), fill=255)
d = ImageDraw.Draw(sheet)
for i, f in enumerate(files):
    im = Image.open(f).convert('RGB')
    x = pad + (i % cols) * (s + pad); y = pad + (i // cols) * (s + pad + lab)
    bezel = Image.new('RGB', (s, s), (235, 235, 235)); bezel.paste(im, (0, 0), mask)
    sheet.paste(bezel, (x, y))
    d.text((x + 4, y + s + 4), f.split('/')[-1][:-4], fill=(40, 40, 40))
sheet.save(sys.argv[2])
