import Foundation

/// Git 文件状态
enum GitFileStatus {
    case unmodified, modified, added, deleted, renamed, untracked
}
