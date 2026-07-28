using System.Text;
using UnityEngine;
using NovelSim.Network;
using NovelSim.World;

namespace NovelSim.UI
{
    public sealed class NovelSimHud : MonoBehaviour
    {
        private WorldSessionManager session;
        private NovelSimApiClient api;
        private string apiUrl;
        private string actionText = "观察四周并询问守卫";
        private string status = "正在初始化……";
        private string error = string.Empty;
        private string narrative = string.Empty;
        private string interactionHint = string.Empty;

        public void Configure(
            WorldSessionManager manager,
            NovelSimApiClient apiClient)
        {
            session = manager;
            api = apiClient;
            apiUrl = api.BaseUrl;
            session.StatusChanged += OnStatusChanged;
            session.ErrorRaised += OnErrorRaised;
            session.SessionChanged += OnSessionChanged;
            session.TurnCompleted += OnTurnCompleted;
        }

        public void SetInteractionHint(string value)
        {
            interactionHint = value ?? string.Empty;
        }

        private void OnDestroy()
        {
            if (session == null)
            {
                return;
            }
            session.StatusChanged -= OnStatusChanged;
            session.ErrorRaised -= OnErrorRaised;
            session.SessionChanged -= OnSessionChanged;
            session.TurnCompleted -= OnTurnCompleted;
        }

        private void OnGUI()
        {
            GUILayout.BeginArea(
                new Rect(16f, 16f, 520f, Mathf.Max(300f, Screen.height - 32f)),
                GUI.skin.box);
            GUILayout.Label("NovelSim · Unity 3D 竖切片");
            GUILayout.Label("WASD 移动 · 靠近 NPC 后按 E · 服务端状态为权威");
            GUILayout.Space(8f);

            GUILayout.Label("FastAPI 地址");
            GUILayout.BeginHorizontal();
            apiUrl = GUILayout.TextField(apiUrl);
            if (GUILayout.Button("应用", GUILayout.Width(64f)))
            {
                api.Configure(apiUrl);
                PlayerPrefs.SetString("NovelSim.ApiBaseUrl", api.BaseUrl);
                status = $"服务地址已切换为 {api.BaseUrl}";
                error = string.Empty;
            }
            GUILayout.EndHorizontal();

            GUI.enabled = session != null && !session.Busy;
            GUILayout.BeginHorizontal();
            if (GUILayout.Button("新建世界线"))
            {
                session.StartNewSession();
            }
            if (GUILayout.Button("提交行动"))
            {
                session.SubmitAction(actionText);
            }
            GUILayout.EndHorizontal();
            actionText = GUILayout.TextField(actionText);
            GUI.enabled = true;

            GUILayout.Space(8f);
            GUILayout.Label(status);
            if (!string.IsNullOrWhiteSpace(error))
            {
                GUILayout.Label($"错误：{error}");
            }
            if (session?.State != null)
            {
                GUILayout.Label(
                    $"世界线 {session.State.timeline_id} · v{session.State.version}");
                GUILayout.Label(
                    $"时间 {session.State.world_time} · 场景 {session.State.current_scene_id}");
            }

            if (!string.IsNullOrWhiteSpace(narrative))
            {
                GUILayout.Space(8f);
                GUILayout.Label("剧情回传");
                GUILayout.TextArea(narrative, GUILayout.MinHeight(120f));
            }
            GUILayout.EndArea();

            if (!string.IsNullOrWhiteSpace(interactionHint))
            {
                var width = 360f;
                GUI.Box(
                    new Rect(
                        (Screen.width - width) * 0.5f,
                        Screen.height - 70f,
                        width,
                        42f),
                    interactionHint);
            }
        }

        private void OnStatusChanged(string value)
        {
            status = value;
            if (!value.Contains("失败"))
            {
                error = string.Empty;
            }
        }

        private void OnErrorRaised(string value)
        {
            error = value;
        }

        private void OnSessionChanged(SessionResponse response)
        {
            narrative = response.resumed
                ? $"已恢复存档：{response.save?.name}"
                : $"已进入：{response.world_meta?.scenario}";
        }

        private void OnTurnCompleted(TurnResponse response)
        {
            var builder = new StringBuilder();
            if (!string.IsNullOrWhiteSpace(response.narrative?.narration))
            {
                builder.AppendLine(response.narrative.narration);
            }
            if (response.narrative?.dialogues != null)
            {
                foreach (var dialogue in response.narrative.dialogues)
                {
                    builder.AppendLine($"{dialogue.speaker_id}：{dialogue.line}");
                }
            }
            if (!string.IsNullOrWhiteSpace(response.rule_reason))
            {
                builder.AppendLine($"规则：{response.rule_reason}");
            }
            narrative = builder.Length == 0
                ? response.error ?? "回合已完成。"
                : builder.ToString().Trim();
        }
    }
}
