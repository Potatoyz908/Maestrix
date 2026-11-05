import Foundation
#if canImport(Glibc)
import Glibc
#endif

extension FileManager {
    /// 原子替换：用 `tmp` 覆盖已存在的 `url`。
    /// macOS 保留 replaceItem 语义；Linux 使用 POSIX rename(2)（同文件系统内原子）。
    func atomicReplaceItem(at url: URL, withItemAt tmp: URL) throws {
        #if os(Linux)
        guard rename(tmp.path, url.path) == 0 else {
            throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno),
                          userInfo: [NSLocalizedDescriptionKey: String(cString: strerror(errno))])
        }
        #else
        _ = try replaceItem(at: url, withItemAt: tmp, backupItemName: nil, resultingItemURL: nil)
        #endif
    }
}
