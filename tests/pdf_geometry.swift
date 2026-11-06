import Foundation
import PDFKit
guard CommandLine.arguments.count == 3, let doc = PDFDocument(url: URL(fileURLWithPath: CommandLine.arguments[1])) else { exit(1) }
let labels = ["Receipt", "Invoice number", "Receipt number", "Date paid", "Bill to", "paid on", "Description", "Qty", "Unit price", "Amount", "Subtotal", "Total", "Amount paid", "Payment history", "Payment method", "Date", "Page 1 of 1"]
var out: [[String:Any]] = []
for i in 0..<doc.pageCount {
 guard let page=doc.page(at:i) else {continue}
 let height=page.bounds(for:.mediaBox).height
 for label in labels {
  for selection in doc.findString(label,withOptions:.caseInsensitive) {
   if !selection.pages.contains(page) {continue}
   let b=selection.bounds(for:page)
   out.append(["text":label,"page":i+1,"x":b.minX,"y":height-b.maxY,"width":b.width,"height":b.height])
  }
 }
}
let result: [String:Any] = ["pages":doc.pageCount,"labels":out]
let data=try JSONSerialization.data(withJSONObject:result,options:[.prettyPrinted,.sortedKeys])
try data.write(to:URL(fileURLWithPath:CommandLine.arguments[2]))
