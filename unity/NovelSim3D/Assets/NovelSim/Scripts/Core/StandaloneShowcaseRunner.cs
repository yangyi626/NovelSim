using System;
using System.Collections;
using System.Globalization;
using System.IO;
using UnityEngine;
using NovelSim.Characters;
using NovelSim.Interaction;
using NovelSim.Network;
using NovelSim.UI;
using NovelSim.World;

namespace NovelSim.Core
{
    /// <summary>
    /// Drives a visible, real-HTTP portfolio take when explicitly requested.
    /// It never runs during normal play, tests, builds, or the headless smoke.
    /// </summary>
    internal sealed class StandaloneShowcaseRunner : MonoBehaviour
    {
        private const float SessionTimeout = 120f;
        private const float TurnTimeout = 360f;
        private const float PresentationTimeout = 60f;
        private const float TailSeconds = 4f;

        private WorldSessionManager session;
        private PlayerInteractor interactor;
        private InteractionTarget target;
        private ToolEventDispatcher dispatcher;
        private NovelSimHud hud;
        private string reportPath;
        private float requestedDuration;
        private float takeStarted;

        public void Configure(
            WorldSessionManager manager,
            PlayerInteractor playerInteractor,
            InteractionTarget interactionTarget,
            ToolEventDispatcher presentationDispatcher,
            NovelSimHud targetHud)
        {
            var args = Environment.GetCommandLineArgs();
            if (!HasArgument(args, "-novelsim-showcase"))
            {
                return;
            }
            session = manager;
            interactor = playerInteractor;
            target = interactionTarget;
            dispatcher = presentationDispatcher;
            hud = targetHud;
            reportPath = ArgumentValue(
                args,
                "-novelsim-showcase-report");
            requestedDuration = ParseDuration(
                ArgumentValue(args, "-novelsim-showcase-duration"));
            WorldSessionManager.ClearSavedSession();
            Application.targetFrameRate = 30;
            StartCoroutine(Run());
        }

        private IEnumerator Run()
        {
            takeStarted = Time.realtimeSinceStartup;
            hud.SetShowcaseOverlay(
                "LIVE RUNTIME · ONE CONTINUOUS TAKE",
                "NovelSim 服务器权威多 Agent 叙事",
                "LLM 只提出候选行动；确定性运行时决定什么真正发生。");

            var waitStarted = Time.realtimeSinceStartup;
            while (!session.HasSession)
            {
                if (!string.IsNullOrWhiteSpace(session.LastError))
                {
                    yield return Finish(
                        "failed",
                        $"进入世界失败：{session.LastError}",
                        2,
                        string.Empty,
                        0,
                        string.Empty,
                        0);
                    yield break;
                }
                if (Time.realtimeSinceStartup - waitStarted > SessionTimeout)
                {
                    yield return Finish(
                        "failed",
                        "等待权威世界线超时",
                        3,
                        string.Empty,
                        0,
                        string.Empty,
                        0);
                    yield break;
                }
                yield return null;
            }

            yield return Hold(
                5f,
                "01 / 05 · AUTHORITY",
                "SQLite WorldState + WorldEvent 是唯一事实源",
                $"新世界线 {session.SessionId} · v{session.State?.version ?? 0}");

            var animator =
                interactor.GetComponent<StylizedCharacterAnimator>();
            var start = interactor.transform.position;
            var end = target.transform.position
                + target.transform.forward * 1.25f;
            var approachStarted = Time.realtimeSinceStartup;
            while (Time.realtimeSinceStartup - approachStarted < 6f)
            {
                var progress = Mathf.Clamp01(
                    (Time.realtimeSinceStartup - approachStarted) / 6f);
                var eased = progress * progress * (3f - 2f * progress);
                interactor.transform.position = Vector3.Lerp(
                    start,
                    end,
                    eased);
                var direction =
                    target.transform.position - interactor.transform.position;
                direction.y = 0f;
                if (direction.sqrMagnitude > 0.001f)
                {
                    interactor.transform.rotation = Quaternion.Slerp(
                        interactor.transform.rotation,
                        Quaternion.LookRotation(direction.normalized),
                        8f * Time.deltaTime);
                }
                animator?.SetLocomotion(0.72f);
                hud.SetShowcaseOverlay(
                    "02 / 05 · REAL UNITY EXECUTION",
                    "玩家接近 NPC，触发与 E 键相同的真实交互",
                    "第三人称移动 · NavMesh NPC · 服务端回合 · 无预写结局");
                Physics.SyncTransforms();
                yield return null;
            }
            animator?.SetLocomotion(0f);
            Physics.SyncTransforms();
            yield return null;

            if (!interactor.TryInteract())
            {
                yield return Finish(
                    "failed",
                    "可视化演示未进入有效交互范围",
                    4,
                    session.SessionId,
                    session.State?.version ?? 0,
                    string.Empty,
                    dispatcher?.LastAcknowledgedSequence ?? 0L);
                yield break;
            }
            hud.SetShowcaseOverlay(
                "02 / 05 · REAL UNITY EXECUTION",
                "真实模型正在解析候选行动",
                "候选 ToolCall 仍须通过实体、能力、时空、认知和因果门禁。");
            waitStarted = Time.realtimeSinceStartup;
            while (session.Busy)
            {
                if (!string.IsNullOrWhiteSpace(session.LastError))
                {
                    yield return Finish(
                        "failed",
                        $"真实交互失败：{session.LastError}",
                        5,
                        session.SessionId,
                        session.State?.version ?? 0,
                        string.Empty,
                        dispatcher?.LastAcknowledgedSequence ?? 0L);
                    yield break;
                }
                if (Time.realtimeSinceStartup - waitStarted > TurnTimeout)
                {
                    yield return Finish(
                        "failed",
                        "等待真实模型回合超时",
                        6,
                        session.SessionId,
                        session.State?.version ?? 0,
                        string.Empty,
                        dispatcher?.LastAcknowledgedSequence ?? 0L);
                    yield break;
                }
                yield return null;
            }
            var committedVersion = session.State?.version ?? 0;
            if (committedVersion < 1)
            {
                yield return Finish(
                    "failed",
                    "交互未提交权威事件",
                    7,
                    session.SessionId,
                    committedVersion,
                    session.LastTurn?.rejection_code ?? string.Empty,
                    dispatcher?.LastAcknowledgedSequence ?? 0L);
                yield break;
            }

            waitStarted = Time.realtimeSinceStartup;
            var expectedSequence = committedVersion * 1000L + 1L;
            while (
                dispatcher != null
                && (
                    dispatcher.IsSyncing
                    || dispatcher.DispatchedCommandCount < 1
                    || dispatcher.LastAcknowledgedSequence < expectedSequence))
            {
                if (
                    Time.realtimeSinceStartup - waitStarted
                    > PresentationTimeout)
                {
                    yield return Finish(
                        "failed",
                        "已提交事件未被 Unity 表现层消费",
                        8,
                        session.SessionId,
                        committedVersion,
                        string.Empty,
                        dispatcher.LastAcknowledgedSequence);
                    yield break;
                }
                yield return null;
            }
            yield return Hold(
                7f,
                "03 / 05 · COMMITTED EVENT",
                $"权威世界已推进到 v{committedVersion}",
                $"Unity 已消费 {dispatcher?.DispatchedCommandCount ?? 0} 条表现命令"
                + $" · cursor {dispatcher?.LastAcknowledgedSequence ?? 0L}");

            var versionBeforeForbidden = session.State?.version ?? 0;
            hud.SetShowcaseOverlay(
                "04 / 05 · ADVERSARIAL INPUT",
                "输入：夜轻歌开飞机飞走了",
                "古代世界未注册现代飞机；失败意图不能成为世界事实。");
            session.SubmitAction("夜轻歌开飞机飞走了");
            waitStarted = Time.realtimeSinceStartup;
            while (session.Busy)
            {
                if (Time.realtimeSinceStartup - waitStarted > TurnTimeout)
                {
                    yield return Finish(
                        "failed",
                        "等待规则拒绝超时",
                        9,
                        session.SessionId,
                        session.State?.version ?? 0,
                        string.Empty,
                        dispatcher?.LastAcknowledgedSequence ?? 0L);
                    yield break;
                }
                yield return null;
            }
            var forbiddenCode =
                session.LastTurn?.rejection_code ?? string.Empty;
            var forbiddenVersion = session.State?.version ?? 0;
            if (
                forbiddenCode != "WORLD_CONCEPT_UNAVAILABLE"
                || forbiddenVersion != versionBeforeForbidden)
            {
                yield return Finish(
                    "failed",
                    "不可能行动未按世界概念门禁拒绝",
                    10,
                    session.SessionId,
                    forbiddenVersion,
                    forbiddenCode,
                    dispatcher?.LastAcknowledgedSequence ?? 0L);
                yield break;
            }
            yield return Hold(
                8f,
                "04 / 05 · REJECTED BEFORE COMMIT",
                "WORLD_CONCEPT_UNAVAILABLE",
                $"世界仍为 v{forbiddenVersion} · 无 StatePatch · 无飞行动画");

            var stableSessionId = session.SessionId;
            hud.SetDeveloperPanelVisible(true);
            hud.SetShowcaseOverlay(
                "05 / 05 · RECONNECT",
                "重新读取服务端权威快照",
                $"session {stableSessionId} · expected v{forbiddenVersion}");
            session.ResumeSession(stableSessionId);
            waitStarted = Time.realtimeSinceStartup;
            while (session.Busy)
            {
                if (Time.realtimeSinceStartup - waitStarted > SessionTimeout)
                {
                    yield return Finish(
                        "failed",
                        "重连权威世界线超时",
                        11,
                        stableSessionId,
                        session.State?.version ?? 0,
                        forbiddenCode,
                        dispatcher?.LastAcknowledgedSequence ?? 0L);
                    yield break;
                }
                yield return null;
            }
            if (
                session.SessionId != stableSessionId
                || (session.State?.version ?? 0) != forbiddenVersion)
            {
                yield return Finish(
                    "failed",
                    "重连没有恢复相同 session/version",
                    12,
                    session.SessionId,
                    session.State?.version ?? 0,
                    forbiddenCode,
                    dispatcher?.LastAcknowledgedSequence ?? 0L);
                yield break;
            }

            var slides = new[]
            {
                new[]
                {
                    "MEASURED · WORLD GATES",
                    "违规探针放过数：G0 6 → G1 4 → G2 1 → G3 0",
                    "提示词不是安全边界；规则、能力、因果和叙事依据逐层收口。",
                },
                new[]
                {
                    "MEASURED · REAL LLM",
                    "固定场景 20/20 完整运行，18/20 目标成功",
                    "45 次调用 · 49,255 Token · 两条失败样本原样保留。",
                },
                new[]
                {
                    "MEASURED · REPRODUCIBILITY",
                    "324 个本地确定性测试 · 9/9 固定评测",
                    "事件回放、因果证据、传播证据链均由结构化报告验证。",
                },
                new[]
                {
                    "ORIGINAL MULTI-AGENT WORLD",
                    "密信：观察 → 逐跳传播 → 共享证据 → 确定性联盟",
                    "公开、销毁或截走密信会形成不同的可回放终态。",
                },
            };
            var slideIndex = 0;
            while (
                Time.realtimeSinceStartup - takeStarted
                < requestedDuration)
            {
                var slide = slides[slideIndex % slides.Length];
                yield return Hold(7f, slide[0], slide[1], slide[2]);
                slideIndex++;
            }

            yield return Finish(
                "passed",
                "真实 Unity 连续演示完成",
                0,
                stableSessionId,
                forbiddenVersion,
                forbiddenCode,
                dispatcher?.LastAcknowledgedSequence ?? 0L);
        }

        private IEnumerator Hold(
            float seconds,
            string eyebrow,
            string title,
            string detail)
        {
            hud.SetShowcaseOverlay(eyebrow, title, detail);
            var until = Time.realtimeSinceStartup + seconds;
            while (Time.realtimeSinceStartup < until)
            {
                yield return null;
            }
        }

        private IEnumerator Finish(
            string status,
            string message,
            int exitCode,
            string sessionId,
            int version,
            string rejectionCode,
            long presentationSequence)
        {
            var elapsed = Time.realtimeSinceStartup - takeStarted;
            var report = new ShowcaseReport
            {
                status = status,
                message = message,
                session_id = sessionId ?? string.Empty,
                version = version,
                rejection_code = rejectionCode ?? string.Empty,
                presentation_sequence = presentationSequence,
                presentation_commands =
                    dispatcher?.DispatchedCommandCount ?? 0,
                duration_seconds = elapsed,
            };
            if (!string.IsNullOrWhiteSpace(reportPath))
            {
                var fullPath = Path.GetFullPath(reportPath);
                var directory = Path.GetDirectoryName(fullPath);
                if (!string.IsNullOrWhiteSpace(directory))
                {
                    Directory.CreateDirectory(directory);
                }
                File.WriteAllText(
                    fullPath,
                    JsonUtility.ToJson(report, true));
            }
            hud.SetShowcaseOverlay(
                status == "passed"
                    ? "PORTFOLIO TAKE · VERIFIED"
                    : "PORTFOLIO TAKE · FAILED",
                message,
                status == "passed"
                    ? "权威事件、规则拒绝和重连恢复均来自本次连续运行。"
                    : $"exit code {exitCode}");
            Debug.Log(
                $"NOVELSIM_SHOWCASE_{status.ToUpperInvariant()} "
                + $"session={report.session_id} version={report.version}");
            var until = Time.realtimeSinceStartup + TailSeconds;
            while (Time.realtimeSinceStartup < until)
            {
                yield return null;
            }
            Application.Quit(exitCode);
        }

        private static float ParseDuration(string value)
        {
            if (
                float.TryParse(
                    value,
                    NumberStyles.Float,
                    CultureInfo.InvariantCulture,
                    out var parsed))
            {
                return Mathf.Clamp(parsed, 30f, 180f);
            }
            return 50f;
        }

        private static bool HasArgument(string[] args, string name)
        {
            return Array.Exists(
                args,
                item => string.Equals(
                    item,
                    name,
                    StringComparison.OrdinalIgnoreCase));
        }

        private static string ArgumentValue(string[] args, string name)
        {
            for (var index = 0; index + 1 < args.Length; index++)
            {
                if (string.Equals(
                    args[index],
                    name,
                    StringComparison.OrdinalIgnoreCase))
                {
                    return args[index + 1];
                }
            }
            return string.Empty;
        }

        [Serializable]
        private sealed class ShowcaseReport
        {
            public string status;
            public string message;
            public string session_id;
            public int version;
            public string rejection_code;
            public long presentation_sequence;
            public int presentation_commands;
            public float duration_seconds;
        }
    }
}
