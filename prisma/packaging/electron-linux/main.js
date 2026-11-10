const { app, BrowserWindow, dialog, ipcMain, shell } = require("electron");
const fs = require("fs");
const http = require("http");
const net = require("net");
const path = require("path");

const APP_ROOT = __dirname;
const PACKAGE_ROOT = path.resolve(APP_ROOT, "..", "..");
const PYTHON_HOME = path.join(PACKAGE_ROOT, "python-runtime");
const PYTHON = path.join(PYTHON_HOME, "bin", "python3");
const BACKEND_LOG = path.join(APP_ROOT, "log", "backend.log");

let backend = null;
let backendPort = 0;
let mainWindow = null;

app.setName("Prisma");

// CentOS 7 commonly runs with user namespaces disabled. The application does
// not load remote content, so Chromium's renderer sandbox cannot be initialized
// there; allow an explicit opt-in for hardened hosts.
if (process.env.PRISMA_ENABLE_SANDBOX !== "1") {
  app.commandLine.appendSwitch("no-sandbox");
}
app.commandLine.appendSwitch("disable-gpu");
app.commandLine.appendSwitch("disable-dev-shm-usage");
app.disableHardwareAcceleration();

function reservePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : 0;
      server.close((error) => {
        if (error) reject(error);
        else resolve(port);
      });
    });
  });
}

function canConnect(port) {
  return new Promise((resolve) => {
    const socket = net.createConnection({ host: "127.0.0.1", port });
    const finish = (ready) => {
      socket.destroy();
      resolve(ready);
    };
    socket.setTimeout(500);
    socket.once("connect", () => finish(true));
    socket.once("timeout", () => finish(false));
    socket.once("error", () => finish(false));
  });
}

async function waitForBackend(port, child) {
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) {
      throw new Error(`Python backend exited with code ${child.exitCode}`);
    }
    if (await canConnect(port)) return;
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
  throw new Error("Python backend did not start within 30 seconds");
}

async function startBackend() {
  if (!fs.existsSync(PYTHON)) {
    throw new Error(`Bundled Python runtime is missing: ${PYTHON}`);
  }
  fs.mkdirSync(path.dirname(BACKEND_LOG), { recursive: true });
  backendPort = await reservePort();
  const logFd = fs.openSync(BACKEND_LOG, "a");
  try {
    backend = require("child_process").spawn(
      PYTHON,
      ["-s", "-m", "api.server", "--port", String(backendPort)],
      {
        cwd: APP_ROOT,
        env: {
          ...process.env,
          PYTHONHOME: PYTHON_HOME,
          PYTHONNOUSERSITE: "1",
          PYTHONUTF8: "1",
        },
        stdio: ["ignore", logFd, logFd],
      }
    );
  } finally {
    fs.closeSync(logFd);
  }
  backend.once("error", (error) => {
    console.error("Failed to start Python backend:", error);
  });
  backend.once("exit", (code, signal) => {
    if (!app.isQuitting && code !== null) {
      console.error(`Python backend exited: code=${code} signal=${signal}`);
    }
  });
  await waitForBackend(backendPort, backend);
}

function stopBackend() {
  if (!backend || backend.exitCode !== null) return;
  try {
    backend.kill("SIGTERM");
    const child = backend;
    setTimeout(() => {
      if (child.exitCode === null) child.kill("SIGKILL");
    }, 1500).unref();
  } catch (error) {
    console.error("Failed to stop Python backend:", error);
  }
}

function requestBackend({ requestPath, method = "GET", body = "" }) {
  return new Promise((resolve, reject) => {
    if (typeof requestPath !== "string" || !requestPath.startsWith("/")) {
      reject(new Error("Invalid API path"));
      return;
    }
    const payload = Buffer.from(String(body || ""), "utf8");
    const request = http.request(
      {
        host: "127.0.0.1",
        port: backendPort,
        path: requestPath,
        method: String(method || "GET").toUpperCase(),
        headers: {
          "Content-Type": "application/json; charset=utf-8",
          "Content-Length": payload.length,
        },
        timeout: 300000,
      },
      (response) => {
        const chunks = [];
        response.on("data", (chunk) => chunks.push(chunk));
        response.on("end", () => {
          resolve(Buffer.concat(chunks).toString("utf8"));
        });
      }
    );
    request.once("timeout", () => request.destroy(new Error("API request timed out")));
    request.once("error", reject);
    if (payload.length) request.write(payload);
    request.end();
  });
}

async function pickFile(kind) {
  let filters = [];
  if (kind === "input") {
    filters = [
      { name: "Input decks", extensions: ["i", "inp", "txt", "dat", "relap", "deck"] },
      { name: "All files", extensions: ["*"] },
    ];
  } else if (kind === "output") {
    filters = [
      { name: "Output files", extensions: ["out", "outdta", "txt", "log", "dat"] },
      { name: "All files", extensions: ["*"] },
    ];
  } else if (kind === "script" || kind === "check") {
    filters = [
      { name: "Excel workbooks", extensions: ["xlsx"] },
      { name: "All files", extensions: ["*"] },
    ];
  }
  const options = { title: "Select file", properties: ["openFile"] };
  if (filters.length) options.filters = filters;
  const result = await dialog.showOpenDialog(mainWindow, options);
  return result.canceled || !result.filePaths.length ? null : result.filePaths[0];
}

async function pickProjectManifest() {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "Open project - select project.json",
    properties: ["openFile"],
    filters: [
      { name: "Prisma project", extensions: ["json"] },
      { name: "All files", extensions: ["*"] },
    ],
  });
  if (result.canceled || !result.filePaths.length) return null;
  const manifest = path.resolve(result.filePaths[0]);
  if (path.basename(manifest).toLowerCase() !== "project.json") {
    throw new Error("Select the project.json file in the project root");
  }
  if (!fs.statSync(manifest, { throwIfNoEntry: false })?.isFile()) {
    throw new Error("project.json does not exist");
  }
  return path.dirname(manifest);
}

async function openSoftwareLibrary(targetPath) {
  const resolved = path.resolve(String(targetPath || ""));
  if (!fs.statSync(resolved, { throwIfNoEntry: false })?.isDirectory()) {
    throw new Error("Software library directory does not exist");
  }
  const error = await shell.openPath(resolved);
  if (error) throw new Error(error);
}

ipcMain.handle("tauri-invoke", async (_event, command, args = {}) => {
  switch (command) {
    case "get_port":
      return backendPort;
    case "api_proxy":
      return requestBackend({
        requestPath: args.path,
        method: args.method,
        body: args.body,
      });
    case "pick_file":
      return pickFile(args.kind);
    case "pick_project_manifest":
      return pickProjectManifest();
    case "open_software_library":
      await openSoftwareLibrary(args.path);
      return null;
    default:
      throw new Error(`Unknown desktop command: ${command}`);
  }
});

function createWindow() {
  mainWindow = new BrowserWindow({
    title: "Prisma",
    width: 1100,
    height: 780,
    minWidth: 860,
    minHeight: 620,
    show: false,
    backgroundColor: "#f4f6f8",
    webPreferences: {
      preload: path.join(APP_ROOT, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });
  mainWindow.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  mainWindow.once("ready-to-show", () => mainWindow.show());
  mainWindow.loadFile(path.join(APP_ROOT, "ui", "index.html"));
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

const hasSingleInstanceLock = app.requestSingleInstanceLock();
if (!hasSingleInstanceLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (!mainWindow) return;
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
  });

  app.whenReady().then(async () => {
    try {
      await startBackend();
      createWindow();
    } catch (error) {
      dialog.showErrorBox(
        "Prisma startup failed",
        `${error && error.message ? error.message : error}\n\nBackend log: ${BACKEND_LOG}`
      );
      app.quit();
    }
  });
}

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0 && backendPort) createWindow();
});
app.on("window-all-closed", () => app.quit());
app.on("before-quit", () => {
  app.isQuitting = true;
  stopBackend();
});
process.on("exit", stopBackend);
