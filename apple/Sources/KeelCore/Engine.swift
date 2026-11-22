import Foundation
import Observation

/// Starts and owns the bundled Python engine (Contents/Resources/engine/keel-engine).
///
/// The engine prints `KEEL_READY <url>` once its API accepts requests, and exits when its stdin
/// closes, so it never outlives the app: on quit we close the pipe; on a crash the OS does.
/// For development, `KEEL_ENGINE_URL=http://127.0.0.1:8711` uses an already running `pf serve`.
@MainActor
@Observable
public final class Engine {
    public enum State: Equatable {
        case starting
        case ready(URL)
        case failed(String)
    }

    public private(set) var state: State = .starting
    private var process: Process?
    private var input: Pipe?
    private var stopping = false
    private var restarts = 0
    private let executable: URL?
    private let extraEnvironment: [String: String]

    /// - Parameters:
    ///   - executable: the engine binary; defaults to the one inside the app bundle.
    ///   - environment: added to the engine's environment (tests point PF_APP_HOME elsewhere).
    public init(executable: URL? = nil, environment: [String: String] = [:]) {
        self.executable = executable ?? Engine.bundledExecutable
        self.extraEnvironment = environment
    }

    public static var bundledExecutable: URL? {
        Bundle.main.resourceURL?.appendingPathComponent("engine/keel-engine/keel-engine")
    }

    public var baseURL: URL? {
        if case let .ready(url) = state { return url }
        return nil
    }

    public func start() {
        stopping = false
        if let dev = ProcessInfo.processInfo.environment["KEEL_ENGINE_URL"], let url = URL(string: dev) {
            state = .ready(url)
            return
        }
        guard let executable, FileManager.default.isExecutableFile(atPath: executable.path) else {
            state = .failed("Engine not found in the app bundle. Rebuild Keel with `make app`.")
            return
        }
        state = .starting
        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable.standardizedFileURL.path)  // Process needs an absolute path
        var env = ProcessInfo.processInfo.environment
        env["KEEL_APP_BUNDLE"] = Bundle.main.bundlePath
        env["KEEL_APP_PID"] = String(ProcessInfo.processInfo.processIdentifier)
        if let info = Bundle.main.url(forResource: "build-info", withExtension: "json") {
            env["KEEL_BUILD_INFO"] = info.path
        }
        env.merge(extraEnvironment) { _, new in new }
        process.environment = env
        let input = Pipe()
        let output = Pipe()
        process.standardInput = input
        process.standardOutput = output
        process.standardError = FileHandle.nullDevice  // the engine logs to ~/Library/Logs/Keel

        let buffer = LineBuffer()
        output.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let chunk = handle.availableData
            if chunk.isEmpty {
                handle.readabilityHandler = nil
                return
            }
            // Keep draining: a full pipe would block the engine.
            for line in buffer.append(chunk) {
                if line.hasPrefix("KEEL_READY "), let url = URL(string: String(line.dropFirst(11))) {
                    Task { @MainActor in self?.state = .ready(url) }
                }
            }
        }
        process.terminationHandler = { [weak self] proc in
            Task { @MainActor in self?.engineExited(status: proc.terminationStatus) }
        }
        do {
            try process.run()
            self.process = process
            self.input = input
        } catch {
            state = .failed("Engine failed to start: \(error.localizedDescription)")
        }
    }

    private func engineExited(status: Int32) {
        process = nil
        input = nil
        guard !stopping else { return }
        restarts += 1
        if restarts <= 3 {
            state = .starting
            Task { @MainActor in
                try? await Task.sleep(for: .seconds(1))
                self.start()
            }
        } else {
            state = .failed("Engine stopped (exit \(status)). See ~/Library/Logs/Keel/keel.log.")
        }
    }

    /// Ask the engine to exit (closing its stdin) and wait briefly; force it if it lingers.
    public func stop() {
        stopping = true
        try? input?.fileHandleForWriting.close()
        guard let process else { return }
        let deadline = Date().addingTimeInterval(5)
        while process.isRunning && Date() < deadline {
            Thread.sleep(forTimeInterval: 0.05)
        }
        if process.isRunning { process.terminate() }
    }

    public func retry() {
        restarts = 0
        start()
    }
}

/// Splits the engine's stdout into lines. Only touched from the pipe's serial readability handler.
private final class LineBuffer: @unchecked Sendable {
    private var data = Data()

    func append(_ chunk: Data) -> [String] {
        data.append(chunk)
        var lines: [String] = []
        while let newline = data.firstIndex(of: 0x0A) {
            lines.append(String(decoding: data[data.startIndex..<newline], as: UTF8.self))
            data.removeSubrange(data.startIndex...newline)
        }
        return lines
    }
}
