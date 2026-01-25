// UnixSocketServer.swift
// Unix Domain Socket server with length-prefixed JSON protocol

import Foundation

/// Unix Domain Socket server for IPC with Python client
final class UnixSocketServer {
    private let socketPath: String
    private var serverSocket: Int32 = -1
    private var isRunning = false
    private let router: RequestRouter
    private var clientHandlers: [Int32: Task<Void, Never>] = [:]
    private let maxMessageSize = 100 * 1024 * 1024  // 100MB max message

    init(socketPath: String, router: RequestRouter) {
        self.socketPath = socketPath
        self.router = router
    }

    /// Start the server and listen for connections
    func start() throws {
        // Remove existing socket file if present
        unlink(socketPath)

        // Create socket
        serverSocket = socket(AF_UNIX, SOCK_STREAM, 0)
        guard serverSocket >= 0 else {
            throw ServerError.socketCreationFailed(errno: errno)
        }

        // Set socket to non-blocking for async operations
        var flags = fcntl(serverSocket, F_GETFL, 0)
        flags |= O_NONBLOCK
        _ = fcntl(serverSocket, F_SETFL, flags)

        // Bind to socket path
        var addr = sockaddr_un()
        addr.sun_family = sa_family_t(AF_UNIX)
        _ = socketPath.withCString { cString in
            withUnsafeMutablePointer(to: &addr.sun_path) { ptr in
                ptr.withMemoryRebound(to: Int8.self, capacity: 104) { pathPtr in
                    strncpy(pathPtr, cString, 103)
                }
            }
        }

        let bindResult = withUnsafePointer(to: &addr) { addrPtr in
            addrPtr.withMemoryRebound(to: sockaddr.self, capacity: 1) { sockaddrPtr in
                bind(serverSocket, sockaddrPtr, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }

        guard bindResult == 0 else {
            close(serverSocket)
            throw ServerError.bindFailed(path: socketPath, errno: errno)
        }

        // Set socket permissions (owner only)
        chmod(socketPath, 0o600)

        // Listen for connections
        guard listen(serverSocket, 5) == 0 else {
            close(serverSocket)
            throw ServerError.listenFailed(errno: errno)
        }

        isRunning = true
        log("Server started on \(socketPath)")

        // Accept loop
        while isRunning {
            let clientSocket = accept(serverSocket, nil, nil)

            if clientSocket < 0 {
                if errno == EAGAIN || errno == EWOULDBLOCK {
                    // No pending connections, sleep briefly
                    Thread.sleep(forTimeInterval: 0.01)
                    continue
                }
                if isRunning {
                    log("Accept error: \(errno)")
                }
                continue
            }

            // Handle client in a task
            let task = Task {
                await handleClient(socket: clientSocket)
            }
            clientHandlers[clientSocket] = task
        }
    }

    /// Stop the server gracefully
    func stop() {
        log("Stopping server...")
        isRunning = false

        // Cancel all client handlers
        for (socket, task) in clientHandlers {
            task.cancel()
            close(socket)
        }
        clientHandlers.removeAll()

        // Close server socket
        if serverSocket >= 0 {
            close(serverSocket)
            serverSocket = -1
        }

        // Remove socket file
        unlink(socketPath)
        log("Server stopped")
    }

    /// Handle a client connection
    private func handleClient(socket clientSocket: Int32) async {
        defer {
            close(clientSocket)
            clientHandlers.removeValue(forKey: clientSocket)
        }

        log("Client connected (fd: \(clientSocket))")

        while isRunning {
            // Read length prefix (4 bytes, big-endian)
            guard let lengthData = readExactly(socket: clientSocket, count: 4) else {
                log("Client disconnected (fd: \(clientSocket))")
                return
            }

            let messageLength = lengthData.withUnsafeBytes { ptr in
                ptr.load(as: UInt32.self).bigEndian
            }

            guard messageLength > 0 && messageLength < maxMessageSize else {
                log("Invalid message length: \(messageLength)")
                return
            }

            // Read message body
            guard let messageData = readExactly(socket: clientSocket, count: Int(messageLength)) else {
                log("Failed to read message body")
                return
            }

            // Process request and get response
            let responseData = await processRequest(data: messageData)

            // Send response with length prefix
            if !sendResponse(socket: clientSocket, data: responseData) {
                log("Failed to send response")
                return
            }
        }
    }

    /// Read exactly N bytes from socket
    private func readExactly(socket: Int32, count: Int) -> Data? {
        var buffer = Data(count: count)
        var totalRead = 0

        while totalRead < count {
            let remaining = count - totalRead
            let bytesRead = buffer.withUnsafeMutableBytes { ptr in
                read(socket, ptr.baseAddress! + totalRead, remaining)
            }

            if bytesRead <= 0 {
                if bytesRead == 0 {
                    return nil  // Connection closed
                }
                if errno == EAGAIN || errno == EWOULDBLOCK {
                    Thread.sleep(forTimeInterval: 0.001)
                    continue
                }
                return nil  // Error
            }

            totalRead += bytesRead
        }

        return buffer
    }

    /// Send response with length prefix
    private func sendResponse(socket: Int32, data: Data) -> Bool {
        // Prepare length prefix (4 bytes, big-endian)
        var length = UInt32(data.count).bigEndian
        var lengthData = Data(bytes: &length, count: 4)

        // Combine length + data
        lengthData.append(data)

        var totalSent = 0
        while totalSent < lengthData.count {
            let bytesSent = lengthData.withUnsafeBytes { ptr in
                write(socket, ptr.baseAddress! + totalSent, lengthData.count - totalSent)
            }

            if bytesSent <= 0 {
                if errno == EAGAIN || errno == EWOULDBLOCK {
                    Thread.sleep(forTimeInterval: 0.001)
                    continue
                }
                return false
            }

            totalSent += bytesSent
        }

        return true
    }

    /// Process a request and return response data
    private func processRequest(data: Data) async -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]

        do {
            let decoder = JSONDecoder()
            let request = try decoder.decode(Request.self, from: data)

            // Route request to handler
            let response = await router.route(request: request)

            return try encoder.encode(response)

        } catch let decodingError as DecodingError {
            // Malformed request
            log("Decoding error: \(decodingError)")
            let errorResponse = Response<String>(
                id: "unknown",
                error: .malformedRequest,
                detail: "Failed to decode request: \(decodingError.localizedDescription)"
            )
            return (try? encoder.encode(AnyResponse(from: errorResponse))) ?? Data()

        } catch {
            // Internal error
            log("Processing error: \(error)")
            let errorResponse = Response<String>(
                id: "unknown",
                error: .internalError,
                detail: error.localizedDescription
            )
            return (try? encoder.encode(AnyResponse(from: errorResponse))) ?? Data()
        }
    }

    private func log(_ message: String) {
        let timestamp = ISO8601DateFormatter().string(from: Date())
        fputs("[\(timestamp)] \(message)\n", stderr)
    }
}

/// Server-specific errors
enum ServerError: Error, CustomStringConvertible {
    case socketCreationFailed(errno: Int32)
    case bindFailed(path: String, errno: Int32)
    case listenFailed(errno: Int32)

    var description: String {
        switch self {
        case .socketCreationFailed(let err):
            return "Failed to create socket: errno \(err)"
        case .bindFailed(let path, let err):
            return "Failed to bind to \(path): errno \(err)"
        case .listenFailed(let err):
            return "Failed to listen: errno \(err)"
        }
    }
}
