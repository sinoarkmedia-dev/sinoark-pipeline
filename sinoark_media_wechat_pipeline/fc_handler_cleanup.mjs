/**
 * Alibaba Cloud Function Compute handler for monthly-cleanup.
 * Wraps digest/monthly-cleanup.ts with no changes to the original script.
 * Mirrors the cron command:
 *   node --no-warnings=ExperimentalWarning --import tsx/esm digest/monthly-cleanup.ts
 */
import { execSync } from 'child_process';
import { dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));

export const handler = async (event, context) => {
  execSync(
    'node --no-warnings=ExperimentalWarning --import tsx/esm digest/monthly-cleanup.ts',
    { cwd: __dirname, stdio: 'inherit' }
  );
  return 'done';
};
