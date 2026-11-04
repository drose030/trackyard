import BeatTrainCore
import Foundation

// Usage: beattrain-check <notes.mid|notes.chart> [expert|hard|medium|easy]
// Prints {"ppq", "difficulties", "notes": [{"tick", "time", "lane", "row"}]} for the
// chosen difficulty (default Expert), as Beat-Train's song screen would see it.
let args = CommandLine.arguments
guard args.count == 2 || args.count == 3,
      let difficulty = DrumDifficulty(rawValue: args.count == 3 ? args[2] : "expert") else {
    FileHandle.standardError.write(Data("usage: beattrain-check <chart file> [expert|hard|medium|easy]\n".utf8))
    exit(2)
}
let url = URL(fileURLWithPath: args[1])
do {
    let available = try DrumChartLoader.availableDifficulties(from: url).map(\.rawValue)
    let chart = try DrumChartLoader.parse(from: url, difficulty: difficulty)
    let notes = chart.notes.map { note -> [String: Any] in
        ["tick": note.tick, "time": note.timeSec, "lane": note.target.lane, "row": note.target.row.rawValue]
    }
    let json = try JSONSerialization.data(withJSONObject: ["ppq": chart.ppq, "difficulties": available, "notes": notes])
    FileHandle.standardOutput.write(json)
} catch {
    FileHandle.standardError.write(Data("\(error)\n".utf8))
    exit(1)
}
