# Repository Instructions

## Git Push Rule

For this repository, use the verified HTTP/1.1 push path as the primary method.
The default `git push` path has repeatedly timed out or reset in this environment,
so treat it only as a secondary fallback.

Preferred push order:

1. Push the current feature branch first.

   ```powershell
   git -c http.version=HTTP/1.1 push origin <current-branch>
   ```

2. Push the same local HEAD to `main` after the feature branch push succeeds.

   ```powershell
   git -c http.version=HTTP/1.1 push origin HEAD:main
   ```

Use plain `git push` only if the HTTP/1.1 path is unavailable or explicitly
unnecessary for the current environment.
