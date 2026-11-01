// Renders the app icon (moon on a night-blue tile) as an .iconset folder. Usage: swift make_icon.swift out.iconset
import Cocoa
let out = CommandLine.arguments[1]
try? FileManager.default.createDirectory(atPath: out, withIntermediateDirectories: true)
let specs: [(String, Int)] = [("16x16", 16), ("16x16@2x", 32), ("32x32", 32), ("32x32@2x", 64), ("128x128", 128),
                              ("128x128@2x", 256), ("256x256", 256), ("256x256@2x", 512), ("512x512", 512), ("512x512@2x", 1024)]
for (name, s) in specs {
    let size = CGFloat(s)
    let img = NSImage(size: NSSize(width: size, height: size))
    img.lockFocus()
    let tile = NSRect(x: 0, y: 0, width: size, height: size).insetBy(dx: size * 0.06, dy: size * 0.06)
    let path = NSBezierPath(roundedRect: tile, xRadius: size * 0.21, yRadius: size * 0.21)
    let g = NSGradient(starting: NSColor(calibratedRed: 0.16, green: 0.18, blue: 0.42, alpha: 1),
                       ending: NSColor(calibratedRed: 0.05, green: 0.06, blue: 0.18, alpha: 1))!
    g.draw(in: path, angle: -90)
    let cfg = NSImage.SymbolConfiguration(pointSize: size * 0.5, weight: .medium)
        .applying(NSImage.SymbolConfiguration(paletteColors: [NSColor(calibratedRed: 1.0, green: 0.93, blue: 0.70, alpha: 1)]))
    if let sym = NSImage(systemSymbolName: "moon.zzz.fill", accessibilityDescription: nil)?.withSymbolConfiguration(cfg) {
        let r = NSRect(x: (size - sym.size.width) / 2, y: (size - sym.size.height) / 2, width: sym.size.width, height: sym.size.height)
        sym.draw(in: r)
    }
    img.unlockFocus()
    guard let tiff = img.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff),
          let png = rep.representation(using: .png, properties: [:]) else { continue }
    try? png.write(to: URL(fileURLWithPath: "\(out)/icon_\(name).png"))
}
