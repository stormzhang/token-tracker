// token-tracker-managed
/**
 * Token Tracker Pi 状态栏扩展（tt setup 安装，勿手改）。
 *
 * 在 session_start / turn_end 后调用 `~/.config/token-tracker/pi-statusline.py` 渲染一行
 * 会话状态（项目 | Total | Cost | Model | Ctx），用 ctx.ui.setStatus("tt", ...) 显示在 footer。
 * pi.exec 不支持 stdin（core/exec.ts stdio:["ignore",...]），payload 走 argv[1] 的 JSON。
 * 脚本缺失 / 超时 / 解析失败一律 fail-open（清掉状态段即可），绝不影响 pi 主流程。
 */
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";

// tt setup 烘焙时注入：[python 解释器, pi-statusline.py 脚本路径]（绝对路径，不依赖 PATH）
const ARGV: string[] = __TT_PI_STATUSLINE_ARGV__;
const TIMEOUT_MS = 3000;

export default function (pi: ExtensionAPI) {
	async function refresh(ctx: ExtensionContext) {
		if (!ctx.hasUI) return; // print / json 模式无 footer，跳过（扩展仍会被加载）
		try {
			const model = ctx.model;
			const payload = JSON.stringify({
				sessionFile: ctx.sessionManager.getSessionFile() ?? "",
				cwd: ctx.cwd,
				model: model ? `${model.provider}/${model.id}` : "",
				contextWindow: model?.contextWindow ?? 0,
			});
			const result = await pi.exec(ARGV[0], [...ARGV.slice(1), payload], { timeout: TIMEOUT_MS });
			const line = (result.stdout || "").split("\n")[0].trim();
			ctx.ui.setStatus("tt", line || undefined);
		} catch {
			// fail-open：状态栏是锦上添花，任何异常都不上抛
		}
	}

	pi.on("session_start", async (_event, ctx) => {
		await refresh(ctx);
	});

	pi.on("turn_end", async (_event, ctx) => {
		await refresh(ctx);
	});
}
