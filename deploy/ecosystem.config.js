// pm2 process file (alternative to deploy/noor-travels.service – use ONE of them, not both).
//   cd /var/www/noor-travels && pm2 start deploy/ecosystem.config.js && pm2 save
// Paths come from where this file lives, so the project folder can be anywhere.
// The app reads .env itself (python-dotenv), so no secrets here.
const path = require("path");
const root = path.resolve(__dirname, "..");

module.exports = {
  apps: [
    {
      name: "noor-travels",
      cwd: root,
      script: path.join(root, "main.py"),
      interpreter: path.join(root, ".venv/bin/python"),
      exec_mode: "fork",            // uvicorn starts its own workers (WORKERS in .env)
      instances: 1,
      autorestart: true,
      restart_delay: 3000,
      max_restarts: 20,
      kill_timeout: 10000,
      env: { PYTHONUNBUFFERED: "1" },
      out_file: path.join(root, "logs/app.out.log"),
      error_file: path.join(root, "logs/app.err.log"),
      time: true,
    },
  ],
};
