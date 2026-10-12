// Flashes a short text on screen like a key-press visualizer, then exits.
// Usage: osd "<text>"  (visible ~1 s, fades out ~0.3 s)
import AppKit

let text = CommandLine.arguments.dropFirst().joined(separator: " ")
if text.isEmpty { exit(0) }

let visibleSeconds = 1.0
let fadeSeconds = 0.3

let app = NSApplication.shared
app.setActivationPolicy(.accessory)

let label = NSTextField(labelWithString: text)
label.font = NSFont.systemFont(ofSize: 34, weight: .semibold)
label.textColor = .white
label.alignment = .center
label.sizeToFit()

let paddingX: CGFloat = 28
let paddingY: CGFloat = 16
let size = NSSize(width: label.frame.width + paddingX * 2, height: label.frame.height + paddingY * 2)

let screen = NSScreen.main ?? NSScreen.screens.first
let area = screen?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
let origin = NSPoint(x: area.midX - size.width / 2, y: area.minY + 80)

let panel = NSPanel(
    contentRect: NSRect(origin: origin, size: size),
    styleMask: [.borderless, .nonactivatingPanel],
    backing: .buffered,
    defer: false
)
panel.isOpaque = false
panel.backgroundColor = .clear
panel.hasShadow = false
panel.level = .screenSaver
panel.ignoresMouseEvents = true
panel.hidesOnDeactivate = false
panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]

let background = NSView(frame: NSRect(origin: .zero, size: size))
background.wantsLayer = true
background.layer?.backgroundColor = NSColor(white: 0.08, alpha: 0.82).cgColor
background.layer?.cornerRadius = 18
label.frame.origin = NSPoint(x: paddingX, y: paddingY)
background.addSubview(label)
panel.contentView = background

panel.alphaValue = 1
panel.orderFrontRegardless()

DispatchQueue.main.asyncAfter(deadline: .now() + visibleSeconds) {
    NSAnimationContext.runAnimationGroup({ context in
        context.duration = fadeSeconds
        panel.animator().alphaValue = 0
    }, completionHandler: {
        exit(0)
    })
}

app.run()
