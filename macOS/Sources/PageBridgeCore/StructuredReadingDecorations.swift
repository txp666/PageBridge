import AppKit

/// Restore only original stroke pixels. Never repaint a filled box over its translation.
public enum StructuredReadingDecorations {
    public static func clip(_ mask: CGPath, excluding equations: [CGRect], page: CGRect, context: CGContext) {
        context.addPath(mask); context.clip()
        // Inline equation graphics move with their attachment. Do not restore
        // their old fraction bars or vector glyph edges over translated prose.
        for equation in equations {
            let outside = CGMutablePath(); outside.addRect(page); outside.addRect(equation)
            context.addPath(outside); context.clip(using: .evenOdd)
        }
    }

    public static func strokeMask(_ decorations: [StructuredReadingDecoration], edgePadding: CGFloat = 0.5) -> CGPath {
        let mask = CGMutablePath()
        for decoration in decorations where decoration.opacity > 0 {
            let path = CGMutablePath()
            for command in decoration.commands {
                let p = command.points
                switch command.op {
                case "M" where p.count == 2: path.move(to: CGPoint(x: p[0], y: p[1]))
                case "L" where p.count == 2: path.addLine(to: CGPoint(x: p[0], y: p[1]))
                case "C" where p.count == 6:
                    path.addCurve(to: CGPoint(x: p[4], y: p[5]), control1: CGPoint(x: p[0], y: p[1]), control2: CGPoint(x: p[2], y: p[3]))
                case "Z": path.closeSubpath()
                default: break
                }
            }
            let dash = decoration.dash.map { CGFloat($0) }
            let center: CGPath = dash.contains(where: { $0 > 0 })
                ? path.copy(dashingWithPhase: decoration.dashPhase, lengths: dash) : path
            let cap = CGLineCap(rawValue: Int32(decoration.lineCap)) ?? .butt
            let join = CGLineJoin(rawValue: Int32(decoration.lineJoin)) ?? .miter
            // Half a PDF point includes the original raster's antialiased edge.
            let width = decoration.kind == "fill_boundary" ? 1 : max(0.1, decoration.width) + edgePadding
            mask.addPath(center.copy(strokingWithWidth: width,
                                     lineCap: cap, lineJoin: join, miterLimit: 10))
        }
        return mask
    }
}
