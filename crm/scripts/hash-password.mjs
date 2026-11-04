// Usage: npm run hash-password -- 'your password'   -> prints a CRM_PASSWORD_HASH value
import { randomBytes, scryptSync } from "node:crypto";
const pw = process.argv[2];
if (!pw) { console.error("usage: npm run hash-password -- 'your password'"); process.exit(1); }
const salt = randomBytes(16);
console.log(`scrypt:${salt.toString("hex")}:${scryptSync(pw, salt, 32).toString("hex")}`);
