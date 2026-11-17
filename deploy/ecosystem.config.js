// pm2 process file (alternative to deploy/noor-travels.service – use ONE of them, not both).
//   pm2 start deploy/ecosystem.config.js && pm2 save
// The app reads .env itself (python-dotenv), so no secrets here.
module.exports = {
  apps: [
    {
      name: "noor-travels",
      cwd: "/srv/noor-travels",
      script: "main.py",
      interpreter: "/srv/noor-travels/.venv/bin/python",
      exec_mode: "fork",            // uvicorn starts its own workers (WORKERS in .env)
      instances: 1,
      autorestart: true,
      restart_delay: 3000,
      max_restarts: 20,
      kill_timeout: 10000,
      env: { PYTHONUNBUFFERED: "1" },
      out_file: "/srv/noor-travels/logs/app.out.log",
      error_file: "/srv/noor-travels/logs/app.err.log",
      time: true,
    },
  ],
};
