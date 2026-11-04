// Trackyard desktop: starts the editor server (Python) and shows it in a window.
// Packaged, Python with PyTorch and ffmpeg ships in the app's resources (the model
// weights download on the first conversion); in development the repo's .venv is used.

const { app, BrowserWindow, Menu, dialog, ipcMain, shell } = require("electron");
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");

let server = null;
let win = null;
let quitting = false;

function python() {
  if (app.isPackaged) {
    const res = process.resourcesPath;
    const exe = process.platform === "win32" ? path.join(res, "python", "python.exe") : path.join(res, "python", "bin", "python3");
    const data = app.getPath("userData");
    // Bytecode and the model weights (downloaded on the first conversion) go to the
    // user's data folder; the app bundle stays read-only.
    return { exe, env: { TORCH_HOME: path.join(data, "torch"), PYTHONPYCACHEPREFIX: path.join(data, "pycache") } };
  }
  const repo = path.resolve(__dirname, "..");
  const exe = process.platform === "win32" ? path.join(repo, ".venv", "Scripts", "python.exe") : path.join(repo, ".venv", "bin", "python");
  return { exe, env: {} };
}

function cleanEnv() {
  // The user's Python settings must not leak into the bundled Python.
  const env = { ...process.env };
  for (const key of ["PYTHONHOME", "PYTHONPATH", "PYTHONDONTWRITEBYTECODE", "PYTHONSTARTUP", "VIRTUAL_ENV"]) delete env[key];
  return env;
}

function startServer() {
  return new Promise((resolve, reject) => {
    const data = app.getPath("userData");
    fs.mkdirSync(path.join(data, "logs"), { recursive: true });
    const log = fs.createWriteStream(path.join(data, "logs", "server.log"), { flags: "w" });
    const { exe, env } = python();
    // Not "cache": macOS file names ignore case, and Chromium owns "Cache" in this folder.
    const args = ["-m", "trackyard.cli", "edit", "--no-browser", "--exit-with-stdin", "--port", "0", "--cache", path.join(data, "analysis")];
    server = spawn(exe, args, {
      cwd: data,
      windowsHide: true,
      env: { ...cleanEnv(), ...env, PYTHONUNBUFFERED: "1" },
    });
    let out = "";
    server.stdout.on("data", (chunk) => {
      log.write(chunk);
      out += chunk;
      const url = out.match(/http:\/\/127\.0\.0\.1:\d+\//);
      if (url) resolve(url[0]);
    });
    server.stderr.on("data", (chunk) => log.write(chunk));
    server.on("error", reject);
    server.on("exit", (code) => {
      server = null;
      if (!quitting) {
        dialog.showErrorBox("Trackyard stopped", `The Trackyard engine quit (code ${code}). Details: ${path.join(data, "logs", "server.log")}`);
        app.quit();
      }
      reject(new Error(`server exited with ${code}`));
    });
  });
}

function createWindow() {
  win = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 900,
    minHeight: 600,
    backgroundColor: "#15171c",
    title: "Trackyard",
    webPreferences: { preload: path.join(__dirname, "preload.js") },
  });
  win.loadFile(path.join(__dirname, "loading.html"));
  // The editor blocks unloading while there are unsaved changes; ask instead of
  // silently refusing to close.
  win.webContents.on("will-prevent-unload", (e) => {
    const choice = dialog.showMessageBoxSync(win, {
      type: "question",
      buttons: ["Discard changes", "Cancel"],
      defaultId: 1,
      message: "This song has unsaved changes.",
      detail: "Close anyway and lose them?",
    });
    if (choice === 0) e.preventDefault();
  });
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });
}

ipcMain.handle("pick", async (_e, kind, start) => {
  const audio = kind === "audio";
  const result = await dialog.showOpenDialog(win, {
    title: audio ? "Choose a song to convert" : kind === "save" ? "Choose where to save the song" : "Choose a folder",
    defaultPath: start || undefined,
    properties: audio ? ["openFile"] : ["openDirectory", "createDirectory"],
    filters: audio ? [{ name: "Audio", extensions: ["mp3", "wav", "ogg", "flac", "m4a", "opus", "aiff"] }] : undefined,
  });
  return result.canceled ? null : result.filePaths[0];
});

function menu() {
  const mac = process.platform === "darwin";
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    ...(mac ? [{ role: "appMenu" }] : [{ label: "File", submenu: [{ role: "quit" }] }]),
    // Text fields need the native clipboard; the editor itself handles ⌘C/⌘V/⌘Z on notes.
    { label: "Edit", submenu: [{ role: "cut", accelerator: "" }, { role: "copy", accelerator: "" }, { role: "paste", accelerator: "" }, { role: "selectAll", accelerator: "" }] },
    { label: "View", submenu: [{ role: "reload" }, { role: "toggleDevTools" }, { type: "separator" }, { role: "togglefullscreen" }] },
    { role: "windowMenu" },
  ]));
}

app.whenReady().then(async () => {
  menu();
  createWindow();
  try {
    const url = await startServer();
    win.loadURL(url);
  } catch (e) {
    if (!quitting) dialog.showErrorBox("Trackyard couldn't start", String(e));
    app.quit();
  }
});

app.on("before-quit", () => {
  quitting = true;
  if (server) server.kill();
});

app.on("window-all-closed", () => app.quit());
