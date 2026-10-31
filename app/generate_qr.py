import qrcode

img = qrcode.make("https://youtube.com")
img.save("my_qrcode.png")

print("QR code generated successfully!")