// data.js / live_data.js 는 브라우저용 JS 파일(const 선언)이라 JSON으로 바로 파싱할 수 없음.
// vm 컨텍스트에서 그대로 실행한 뒤 원하는 전역 변수를 JSON으로 출력한다.
// 사용법: node export_listings.js <파일경로> <변수명>
const fs = require("fs");
const vm = require("vm");

const [, , filePath, varName] = process.argv;
const code = fs.readFileSync(filePath, "utf8");
const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(code, sandbox);
const value = vm.runInContext(varName, sandbox);
process.stdout.write(JSON.stringify(value));
