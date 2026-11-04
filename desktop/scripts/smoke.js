// Runs scripts/smoke.py with the bundled Python (path differs per platform).
const { execFileSync } = require("child_process");
const os = require("os");
const path = require("path");

const root = path.join(__dirname, "..", "build", "python");
const exe = process.platform === "win32" ? path.join(root, "python.exe") : path.join(root, "bin", "python3");
const env = { ...process.env, TORCH_HOME: path.join(os.tmpdir(), "trackyard-smoke-torch") };
delete env.PYTHONPATH;
delete env.PYTHONHOME;
execFileSync(exe, [path.join(__dirname, "smoke.py")], { stdio: "inherit", env });
