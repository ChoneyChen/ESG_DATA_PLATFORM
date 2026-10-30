// Page text for PDFs without a usable text layer, from the macOS Vision
// framework (on-device; nothing leaves the machine).
//
// usage: vision_ocr <pdf> <pages: all | 0,1,5> <scale> <languages: zh-Hans,en-US> [--words] [--jobs N]
//
// One JSON line per page on stdout, in page order:
//   {"page_index", "width", "height", "seconds", "lines": [{"text", "confidence", "bbox", "words"?}]}
// Geometry is in pixels of the rendered page image, origin top left.
import AppKit
import Foundation
import PDFKit
import Vision

let arguments = CommandLine.arguments
guard arguments.count >= 5 else {
    FileHandle.standardError.write("usage: vision_ocr <pdf> <pages> <scale> <languages> [--words] [--jobs N]\n".data(using: .utf8)!)
    exit(2)
}
guard let document = PDFDocument(url: URL(fileURLWithPath: arguments[1])) else {
    FileHandle.standardError.write("cannot open PDF\n".data(using: .utf8)!)
    exit(3)
}
let pages: [Int] = arguments[2] == "all"
    ? Array(0..<document.pageCount)
    : arguments[2].split(separator: ",").compactMap { Int($0) }.filter { $0 >= 0 && $0 < document.pageCount }
let scale = CGFloat(Double(arguments[3]) ?? 1.5)
let languages = arguments[4].split(separator: ",").map(String.init)
let wantWords = arguments.contains("--words")
var jobs = 3
if let index = arguments.firstIndex(of: "--jobs"), index + 1 < arguments.count, let value = Int(arguments[index + 1]) {
    jobs = max(1, value)
}

func render(_ page: PDFPage) -> CGImage? {
    let box = page.bounds(for: .cropBox)
    let rotated = page.rotation % 180 != 0
    let size = NSSize(width: (rotated ? box.height : box.width) * scale, height: (rotated ? box.width : box.height) * scale)
    let image = page.thumbnail(of: size, for: .cropBox)
    var rect = NSRect(origin: .zero, size: image.size)
    return image.cgImage(forProposedRect: &rect, context: nil, hints: nil)
}

func pixelBox(_ box: CGRect, _ width: Int, _ height: Int) -> [Double] {
    let w = Double(width), h = Double(height)
    return [Double(box.minX) * w, (1 - Double(box.maxY)) * h, Double(box.maxX) * w, (1 - Double(box.minY)) * h]
        .map { ($0 * 10).rounded() / 10 }
}

func recognize(_ image: CGImage, pageIndex: Int) -> [String: Any] {
    let started = Date()
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = languages
    request.usesLanguageCorrection = false
    if #available(macOS 13.0, *) { request.revision = VNRecognizeTextRequestRevision3 }
    let handler = VNImageRequestHandler(cgImage: image, options: [:])
    var lines: [[String: Any]] = []
    do {
        try handler.perform([request])
    } catch {
        return ["page_index": pageIndex, "error": "\(error)"]
    }
    for observation in request.results ?? [] {
        guard let candidate = observation.topCandidates(1).first else { continue }
        var line: [String: Any] = [
            "text": candidate.string,
            "confidence": Double(candidate.confidence),
            "bbox": pixelBox(observation.boundingBox, image.width, image.height),
        ]
        if wantWords {
            var words: [[String: Any]] = []
            let text = candidate.string
            var index = text.startIndex
            while index < text.endIndex {
                while index < text.endIndex && text[index].isWhitespace { index = text.index(after: index) }
                guard index < text.endIndex else { break }
                var end = index
                while end < text.endIndex && !text[end].isWhitespace { end = text.index(after: end) }
                if let box = try? candidate.boundingBox(for: index..<end) {
                    words.append(["text": String(text[index..<end]),
                                  "bbox": pixelBox(box.boundingBox, image.width, image.height)])
                }
                index = end
            }
            line["words"] = words
        }
        lines.append(line)
    }
    return ["page_index": pageIndex, "width": image.width, "height": image.height,
            "seconds": (Date().timeIntervalSince(started) * 100).rounded() / 100, "lines": lines]
}

// Rendering stays on this thread (PDFKit); recognition runs `jobs` pages at a time.
let queue = DispatchQueue(label: "vision-ocr", attributes: .concurrent)
let slots = DispatchSemaphore(value: jobs)
let group = DispatchGroup()
let lock = NSLock()
var results: [Int: [String: Any]] = [:]
var next = 0

func flush() {
    // Emit finished pages in page order as soon as they are contiguous.
    while next < pages.count, let result = results[pages[next]] {
        if let data = try? JSONSerialization.data(withJSONObject: result, options: []),
           let text = String(data: data, encoding: .utf8) {
            print(text)
            fflush(stdout)
        }
        results.removeValue(forKey: pages[next])
        next += 1
    }
}

for pageIndex in pages {
    guard let page = document.page(at: pageIndex), let image = autoreleasepool(invoking: { render(page) }) else {
        lock.lock(); results[pageIndex] = ["page_index": pageIndex, "error": "render_failed"]; flush(); lock.unlock()
        continue
    }
    slots.wait()
    group.enter()
    queue.async {
        let result = autoreleasepool { recognize(image, pageIndex: pageIndex) }
        lock.lock(); results[pageIndex] = result; flush(); lock.unlock()
        slots.signal()
        group.leave()
    }
}
group.wait()
lock.lock(); flush(); lock.unlock()
