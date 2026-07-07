const cp = require('child_process');
const cmd = process.argv.slice(2).join(' ');
if (!cmd) {
  console.error("No command provided");
  process.exit(1);
}
try {
  cp.execSync(cmd, { encoding: 'utf8', stdio: 'inherit' });
} catch (e) {
  process.exit(e.status || 1);
}
