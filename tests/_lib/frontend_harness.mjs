/**
 * 前端行为测试 harness：以最小的 document 与 sessionStorage 替身导入包内真实模块，
 * 按场景落全局状态与表单控件后执行 buildRequestBody，请求体经 stdout 回写 JSON。
 *
 * stdin 读入场景 JSON：{ moduleUrl, tool, configInfo, controls }。
 * moduleUrl 为 generate.js 的 file URL。controls 按元素 id 索引用户触碰过的控件，
 * 取值形如 { value, checked }，未触碰的控件按留空与未勾选取值。
 * stdout 回写一行 JSON：{ body }。
 */

"use strict";

// 未触碰控件的缺省形态。
const UNTOUCHED_CONTROL = { value: "", checked: false };

async function readScenario() {
  process.stdin.setEncoding("utf8");
  let raw = "";
  for await (const chunk of process.stdin) raw += chunk;
  return JSON.parse(raw);
}

const scenario = await readScenario();

const controls = new Map(
  Object.entries(scenario.controls ?? {}).map(([id, touched]) => [
    id,
    { ...UNTOUCHED_CONTROL, ...touched },
  ]),
);

// api.js 导入期读 sessionStorage 取令牌，取空即可。
globalThis.sessionStorage = {
  getItem: () => null,
  setItem() {},
  removeItem() {},
};
globalThis.document = {
  getElementById: (id) => controls.get(id) ?? UNTOUCHED_CONTROL,
};

const generate = await import(scenario.moduleUrl);
// 全局状态 state 由 api.js 持有，须经同一 file URL 取到 generate.js 所用的同一实例。
const { state } = await import(new URL("./api.js", scenario.moduleUrl).href);
state.tool = scenario.tool;
state.configInfo = scenario.configInfo;

process.stdout.write(`${JSON.stringify({ body: generate.buildRequestBody() })}\n`);
