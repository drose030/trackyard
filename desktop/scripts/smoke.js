// Runs scripts/smoke.py with the bundled Python (path differs per platform), with stdin
// as an open pipe like the app's, and fails if it hangs.
const { spawn } = require("child_process");
const os = require("os");
const path = require("path");

const root = path.join(__dirname, "..", "build", "python");
const exe = process.platform === "win32" ? path.join(root, "python.exe") : path.join(root, "bin", "python3");
const env = { ...process.env, TORCH_HOME: path.join(os.tmpdir(), "trackyard-smoke-torch") };
delete env.PYTHONPATH;
delete env.PYTHONHOME;
const child = spawn(exe, [path.join(__dirname, "smoke.py")], { stdio: ["pipe", "inherit", "inherit"], env });
const timer = setTimeout(() => {
  console.error("smoke test timed out");
  child.kill();
}, 20 * 60 * 1000);
child.on("exit", (code) => {
  clearTimeout(timer);
  process.exit(code ?? 1);
});
