import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const directory = resolve(fileURLToPath(new URL("..", import.meta.url)));
const repository = resolve(directory, "..");
const pythonPath = [resolve(repository, "bridge"), process.env.PYTHONPATH]
  .filter(Boolean)
  .join(process.platform === "win32" ? ";" : ":");
const python = process.env.PYTHON
  ? resolve(directory, process.env.PYTHON)
  : (process.platform === "win32" ? "python" : "python3");
const schema = spawnSync(
  python,
  [resolve(repository, "bridge/export_owner_openapi.py")],
  {
    cwd: repository,
    encoding: "utf8",
    env: { ...process.env, PYTHONPATH: pythonPath },
  },
);

if (schema.status !== 0) {
  process.stderr.write(schema.stderr || "Unable to export FastAPI OpenAPI schema.\n");
  process.exit(schema.status || 1);
}

const temporary = mkdtempSync(join(tmpdir(), "commerce-owner-openapi-"));
const input = join(temporary, "openapi.json");
writeFileSync(input, schema.stdout);
const result = spawnSync(
  process.execPath,
  [
    resolve(directory, "node_modules/openapi-typescript/bin/cli.js"),
    input,
    "-o",
    resolve(directory, "src/api/generated.ts"),
  ],
  { cwd: directory, stdio: "inherit" },
);
rmSync(temporary, { recursive: true, force: true });
process.exit(result.status || 0);
