// dev.mjs — 开发模式：vite build --watch（自动重建 renderer）+ Electron 热重载
// 用法：npm run dev
// - 启动 vite build --watch 监听 renderer 源码变化自动重建
// - 等 vite 首次构建完成后再启动 Electron（--dev 标志触发 main.cjs 的 index.html 热重载）
// - 任一子进程退出则清理另一个并退出
import { spawn, spawnSync } from "node:child_process"
import { existsSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(__dirname, "..")
const distIndex = path.join(root, "dist", "index.html")
const viteBin = path.join(root, "node_modules", "vite", "bin", "vite.js")
const electronBin = path.join(root, "node_modules", "electron", "dist", "electron.exe")

// 首次确保 dist 就绪（vite watch 首次构建前 dist 可能不存在）
if (!existsSync(distIndex)) {
  console.log("[dev] 首次构建 renderer…")
  const r = spawnSync(process.execPath, [viteBin, "build"], { cwd: root, stdio: "inherit" })
  if (r.status !== 0) {
    console.error("[dev] 首次构建失败，请检查 vite 依赖（如 @rollup/rollup-win32-x64-msvc 缺失）")
    process.exit(1)
  }
}

// vite build --watch（捕获 stdout 以识别首次构建完成的 "built in" 标记）
const vite = spawn(process.execPath, [viteBin, "build", "--watch"], {
  cwd: root,
  stdio: ["ignore", "pipe", "inherit"],
})

let firstBuildDone = false
let electron = null

function startElectron() {
  electron = spawn(electronBin, [".", "--dev", "--remote-debugging-port=9223"], { cwd: root, stdio: "inherit" })
  electron.on("exit", (code) => {
    console.log(`[dev] electron 退出 (code=${code})`)
    cleanup()
  })
}

vite.stdout.on("data", (d) => {
  process.stdout.write(d)
  if (!firstBuildDone && /built in/i.test(d.toString())) {
    firstBuildDone = true
    startElectron()
  }
})

let closing = false
function cleanup() {
  if (closing) return
  closing = true
  try { vite.kill() } catch { /* 忽略 */ }
  try { electron?.kill() } catch { /* 忽略 */ }
  process.exit(0)
}

process.on("SIGINT", cleanup)
process.on("SIGTERM", cleanup)

vite.on("exit", (code) => {
  console.log(`[dev] vite 退出 (code=${code})`)
  cleanup()
})
