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
        private string worldTitle = "华容巷 · 暴雨夜";
        private string currentObjective =
            "穿过雨幕，向守卫打听华容巷刚才发生的事。";
        private string actionText = "观察四周并询问守卫";
        private string status = "正在初始化……";
        private string error = string.Empty;
        private string narrative =
            "暴雨洗过华容巷的青石，檐下灯火在积水里摇晃。"
            + "守卫按住刀柄，正审视着每一个靠近的人。";
        private string interactionHint = string.Empty;
        private string interactionFeedback = string.Empty;
        private float interactionFeedbackUntil;
        private string allianceStatus = string.Empty;
        private bool showcaseOverlay;
        private string showcaseEyebrow = string.Empty;
        private string showcaseTitle = string.Empty;
        private string showcaseDetail = string.Empty;
        private bool developerPanel;
        private Texture2D panelTexture;
        private Texture2D softPanelTexture;
        private Texture2D accentTexture;
        private Texture2D dangerTexture;
        private Texture2D buttonTexture;
        private Texture2D buttonHoverTexture;
        private GUIStyle titleStyle;
        private GUIStyle eyebrowStyle;
        private GUIStyle bodyStyle;
        private GUIStyle mutedStyle;
        private GUIStyle narrativeStyle;
        private GUIStyle statusStyle;
        private GUIStyle hintStyle;
        private GUIStyle inputStyle;
        private GUIStyle buttonStyle;

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
            session.SceneRunCompleted += OnSceneRunCompleted;
        }

        public void SetInteractionHint(string value)
        {
            interactionHint = value ?? string.Empty;
        }

        public void SetInteractionFeedback(string value)
        {
            interactionFeedback = value ?? string.Empty;
            interactionFeedbackUntil = Time.unscaledTime + 2.4f;
        }

        public void ShowPresentationMessage(string value)
        {
            SetInteractionFeedback(value);
        }

        public void SetAllianceStatus(string value)
        {
            allianceStatus = value ?? string.Empty;
        }

        public void SetShowcaseOverlay(
            string eyebrow,
            string title,
            string detail)
        {
            showcaseOverlay = true;
            showcaseEyebrow = eyebrow ?? string.Empty;
            showcaseTitle = title ?? string.Empty;
            showcaseDetail = detail ?? string.Empty;
        }

        public void ClearShowcaseOverlay()
        {
            showcaseOverlay = false;
        }

        public void SetDeveloperPanelVisible(bool value)
        {
            developerPanel = value;
        }

        private void OnDestroy()
        {
            if (session != null)
            {
                session.StatusChanged -= OnStatusChanged;
                session.ErrorRaised -= OnErrorRaised;
                session.SessionChanged -= OnSessionChanged;
                session.TurnCompleted -= OnTurnCompleted;
                session.SceneRunCompleted -= OnSceneRunCompleted;
            }
            DestroyTexture(panelTexture);
            DestroyTexture(softPanelTexture);
            DestroyTexture(accentTexture);
            DestroyTexture(dangerTexture);
            DestroyTexture(buttonTexture);
            DestroyTexture(buttonHoverTexture);
        }

        private void OnGUI()
        {
            EnsureStyles();
            ToggleDeveloperPanel();
            GUI.depth = -20;

            DrawHeader();
            DrawObjective();
            DrawNarrative();
            DrawControls();
            DrawInteractionHint();
            if (showcaseOverlay)
            {
                DrawShowcaseOverlay();
            }
            if (developerPanel)
            {
                DrawDeveloperPanel();
            }
        }

        private void DrawShowcaseOverlay()
        {
            var width = Mathf.Min(760f, Screen.width - 48f);
            var panel = new Rect(
                (Screen.width - width) * 0.5f,
                94f,
                width,
                112f);
            GUI.DrawTexture(panel, panelTexture);
            GUI.DrawTexture(
                new Rect(panel.x, panel.y, 5f, panel.height),
                accentTexture);
            GUI.Label(
                new Rect(panel.x + 22f, panel.y + 12f, width - 44f, 20f),
                showcaseEyebrow,
                eyebrowStyle);
            GUI.Label(
                new Rect(panel.x + 21f, panel.y + 31f, width - 42f, 34f),
                showcaseTitle,
                titleStyle);
            GUI.Label(
                new Rect(panel.x + 22f, panel.y + 69f, width - 44f, 34f),
                showcaseDetail,
                bodyStyle);
        }

        private void DrawHeader()
        {
            GUI.DrawTexture(
                new Rect(0f, 0f, Screen.width, 78f),
                panelTexture);
            GUI.DrawTexture(
                new Rect(24f, 18f, 3f, 42f),
                accentTexture);
            GUI.Label(
                new Rect(42f, 14f, 460f, 22f),
                "NOVELSIM  /  SERVER-AUTHORITATIVE WORLD",
                eyebrowStyle);
            GUI.Label(
                new Rect(41f, 33f, 460f, 36f),
                worldTitle,
                titleStyle);

            var statusText = session != null && session.Busy
                ? "世界演算中"
                : session?.State != null
                    ? $"已连接 · 世界版本 v{session.State.version}"
                    : status;
            var width = Mathf.Min(310f, Screen.width * 0.32f);
            var statusRect = new Rect(
                Screen.width - width - 26f,
                21f,
                width,
                34f);
            GUI.DrawTexture(
                statusRect,
                string.IsNullOrWhiteSpace(error)
                    ? softPanelTexture
                    : dangerTexture);
            GUI.Label(
                new Rect(
                    statusRect.x + 14f,
                    statusRect.y + 5f,
                    statusRect.width - 28f,
                    24f),
                string.IsNullOrWhiteSpace(error)
                    ? $"●  {statusText}"
                    : $"!  {error}",
                statusStyle);
        }

        private void DrawObjective()
        {
            var panel = new Rect(24f, 94f, 322f, 80f);
            GUI.DrawTexture(panel, softPanelTexture);
            GUI.Label(
                new Rect(panel.x + 18f, panel.y + 13f, 310f, 22f),
                "当前目标",
                eyebrowStyle);
            GUI.Label(
                new Rect(panel.x + 18f, panel.y + 35f, 286f, 36f),
                string.IsNullOrWhiteSpace(allianceStatus)
                    ? currentObjective
                    : allianceStatus,
                bodyStyle);
        }

        private void DrawNarrative()
        {
            var width = Mathf.Min(650f, Screen.width - 48f);
            var height = Mathf.Min(145f, Screen.height * 0.24f);
            var panel = new Rect(
                24f,
                Screen.height - height - 24f,
                width,
                height);
            GUI.DrawTexture(panel, panelTexture);
            GUI.DrawTexture(
                new Rect(panel.x, panel.y, 4f, panel.height),
                accentTexture);
            GUI.Label(
                new Rect(panel.x + 22f, panel.y + 12f, 180f, 20f),
                "世界叙事",
                eyebrowStyle);
            GUI.Label(
                new Rect(
                    panel.x + 22f,
                    panel.y + 36f,
                    panel.width - 44f,
                    panel.height - 48f),
                narrative,
                narrativeStyle);
        }

        private void DrawControls()
        {
            var width = 300f;
            var panel = new Rect(
                Screen.width - width - 24f,
                Screen.height - 82f,
                width,
                58f);
            GUI.DrawTexture(panel, softPanelTexture);
            GUI.Label(
                new Rect(panel.x + 14f, panel.y + 7f, width - 28f, 21f),
                "WASD  移动     Shift  疾跑",
                bodyStyle);
            GUI.Label(
                new Rect(panel.x + 14f, panel.y + 31f, width - 28f, 19f),
                "右键  环视     滚轮  远近     F1  调试",
                mutedStyle);
        }

        private void DrawInteractionHint()
        {
            if (string.IsNullOrWhiteSpace(interactionHint))
            {
                return;
            }
            var width = Mathf.Min(440f, Screen.width - 48f);
            var panel = new Rect(
                (Screen.width - width) * 0.5f,
                Screen.height * 0.61f,
                width,
                70f);
            GUI.DrawTexture(panel, softPanelTexture);
            GUI.DrawTexture(
                new Rect(panel.x + 10f, panel.y + 18f, 34f, 34f),
                accentTexture);
            GUI.Label(
                new Rect(panel.x + 10f, panel.y + 20f, 34f, 30f),
                "E",
                hintStyle);
            GUI.Label(
                new Rect(
                    panel.x + 58f,
                    panel.y + 10f,
                    panel.width - 72f,
                    28f),
                interactionHint.Replace("按 E ", string.Empty),
                bodyStyle);
            if (!string.IsNullOrWhiteSpace(interactionFeedback)
                && Time.unscaledTime < interactionFeedbackUntil)
            {
                GUI.Label(
                    new Rect(
                        panel.x + 58f,
                        panel.y + 40f,
                        panel.width - 72f,
                        18f),
                    interactionFeedback,
                    mutedStyle);
            }
        }

        private void DrawDeveloperPanel()
        {
            var width = Mathf.Min(440f, Screen.width - 48f);
            var panel = new Rect(
                Screen.width - width - 24f,
                112f,
                width,
                358f);
            GUI.DrawTexture(panel, panelTexture);
            GUILayout.BeginArea(new Rect(
                panel.x + 18f,
                panel.y + 15f,
                panel.width - 36f,
                panel.height - 30f));
            GUILayout.Label("世界调试 / F1 关闭", eyebrowStyle);
            GUILayout.Space(8f);
            GUILayout.Label("FastAPI 地址", mutedStyle);
            GUILayout.BeginHorizontal();
            apiUrl = GUILayout.TextField(
                apiUrl,
                inputStyle,
                GUILayout.Height(34f));
            if (GUILayout.Button(
                "应用",
                buttonStyle,
                GUILayout.Width(68f),
                GUILayout.Height(34f)))
            {
                api.Configure(apiUrl);
                PlayerPrefs.SetString("NovelSim.ApiBaseUrl", api.BaseUrl);
                status = $"服务地址已切换为 {api.BaseUrl}";
                error = string.Empty;
            }
            GUILayout.EndHorizontal();
            GUILayout.Space(8f);
            actionText = GUILayout.TextField(
                actionText,
                inputStyle,
                GUILayout.Height(34f));
            GUI.enabled = session != null && !session.Busy;
            GUILayout.BeginHorizontal();
            if (GUILayout.Button(
                "新建世界线",
                buttonStyle,
                GUILayout.Height(34f)))
            {
                session.StartNewSession();
            }
            if (GUILayout.Button(
                "提交行动",
                buttonStyle,
                GUILayout.Height(34f)))
            {
                session.SubmitAction(actionText);
            }
            GUILayout.EndHorizontal();
            GUILayout.Space(8f);
            GUILayout.Label("原创密信三路线（真实 ToolCall）", mutedStyle);
            GUI.enabled = session != null && !session.Busy;
            GUILayout.BeginHorizontal();
            if (GUILayout.Button(
                "销毁密信",
                buttonStyle,
                GUILayout.Height(34f)))
            {
                session.RunSecretLetterRoute("destroy_letter");
            }
            if (GUILayout.Button(
                "携信离开",
                buttonStyle,
                GUILayout.Height(34f)))
            {
                session.RunSecretLetterRoute("intercept_letter");
            }
            if (GUILayout.Button(
                "公开真相",
                buttonStyle,
                GUILayout.Height(34f)))
            {
                session.RunSecretLetterRoute("expose_truth");
            }
            GUILayout.EndHorizontal();
            GUI.enabled = true;
            GUILayout.Space(8f);
            if (session?.State != null)
            {
                GUILayout.Label(
                    $"世界线 {session.State.timeline_id}  ·  "
                    + $"v{session.State.version}",
                    mutedStyle);
                GUILayout.Label(
                    $"时间 {session.State.world_time}  ·  "
                    + $"场景 {session.State.current_scene_id}",
                    mutedStyle);
            }
            GUILayout.EndArea();
        }

        private void ToggleDeveloperPanel()
        {
            var currentEvent = Event.current;
            if (currentEvent.type == EventType.KeyDown
                && currentEvent.keyCode == KeyCode.F1)
            {
                developerPanel = !developerPanel;
                currentEvent.Use();
            }
        }

        private void EnsureStyles()
        {
            if (panelTexture != null)
            {
                return;
            }

            panelTexture = Texture(new Color(0.012f, 0.02f, 0.03f, 0.78f));
            softPanelTexture = Texture(
                new Color(0.035f, 0.06f, 0.075f, 0.66f));
            accentTexture = Texture(new Color(0.83f, 0.47f, 0.14f, 1f));
            dangerTexture = Texture(new Color(0.36f, 0.045f, 0.04f, 0.9f));
            buttonTexture = Texture(new Color(0.11f, 0.18f, 0.21f, 1f));
            buttonHoverTexture = Texture(new Color(0.18f, 0.29f, 0.32f, 1f));

            titleStyle = new GUIStyle(GUI.skin.label)
            {
                fontSize = 24,
                fontStyle = FontStyle.Bold,
                normal = { textColor = new Color(0.95f, 0.9f, 0.78f) },
            };
            eyebrowStyle = new GUIStyle(GUI.skin.label)
            {
                fontSize = 11,
                fontStyle = FontStyle.Bold,
                normal = { textColor = new Color(0.86f, 0.55f, 0.22f) },
            };
            bodyStyle = new GUIStyle(GUI.skin.label)
            {
                fontSize = 15,
                wordWrap = true,
                normal = { textColor = new Color(0.86f, 0.89f, 0.9f) },
            };
            mutedStyle = new GUIStyle(bodyStyle)
            {
                fontSize = 12,
                normal = { textColor = new Color(0.56f, 0.64f, 0.68f) },
            };
            narrativeStyle = new GUIStyle(bodyStyle)
            {
                fontSize = 15,
                padding = new RectOffset(0, 0, 0, 0),
            };
            statusStyle = new GUIStyle(bodyStyle)
            {
                fontSize = 13,
                alignment = TextAnchor.MiddleRight,
                clipping = TextClipping.Clip,
            };
            hintStyle = new GUIStyle(bodyStyle)
            {
                fontSize = 18,
                fontStyle = FontStyle.Bold,
                alignment = TextAnchor.MiddleCenter,
                normal = { textColor = new Color(0.08f, 0.04f, 0.02f) },
            };
            inputStyle = new GUIStyle(GUI.skin.textField)
            {
                fontSize = 13,
                padding = new RectOffset(10, 10, 8, 7),
                normal =
                {
                    background = softPanelTexture,
                    textColor = new Color(0.9f, 0.92f, 0.92f),
                },
                focused =
                {
                    background = softPanelTexture,
                    textColor = Color.white,
                },
            };
            buttonStyle = new GUIStyle(GUI.skin.button)
            {
                fontSize = 13,
                fontStyle = FontStyle.Bold,
                normal =
                {
                    background = buttonTexture,
                    textColor = new Color(0.9f, 0.88f, 0.8f),
                },
                hover =
                {
                    background = buttonHoverTexture,
                    textColor = Color.white,
                },
                active =
                {
                    background = accentTexture,
                    textColor = Color.black,
                },
            };
        }

        private static Texture2D Texture(Color color)
        {
            var texture = new Texture2D(1, 1, TextureFormat.RGBA32, false)
            {
                hideFlags = HideFlags.HideAndDontSave,
            };
            texture.SetPixel(0, 0, color);
            texture.Apply();
            return texture;
        }

        private static void DestroyTexture(Texture2D texture)
        {
            if (texture != null)
            {
                Destroy(texture);
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
            var scenario = response.world_meta?.scenario ?? "未知场景";
            worldTitle = scenario.Contains("密信")
                ? "密信疑云 · 午夜前"
                : "华容巷 · 暴雨夜";
            currentObjective = scenario.Contains("密信")
                ? "选择销毁、截获或公开密信；每条路线都会提交独立世界线。"
                : "穿过雨幕，向守卫打听华容巷刚才发生的事。";
            narrative = response.resumed
                ? $"已恢复存档：{response.save?.name}"
                : "暴雨洗过华容巷的青石，檐下灯火在积水里摇晃。"
                    + $"你已进入{scenario}，"
                    + "守卫按住刀柄，正审视着每一个靠近的人。";
        }

        private void OnSceneRunCompleted(SecretLetterRunResponse response)
        {
            var endingText = response.ending switch
            {
                "letter_destroyed" => "你销毁了唯一密信，传播链被中断。",
                "player_intercepted" => "你携信离开门房，守卫失去证据。",
                "truth_exposed" => "真相完成传播，管家与盟友建立防卫联盟。",
                "defenders_allied" => "NPC 自主传播证据并建立防卫联盟。",
                _ => $"路线结束：{response.ending}",
            };
            narrative =
                $"{endingText}\n权威世界 v{response.state?.version ?? 0}，"
                + $"长期记忆投影 {response.memory_record_count} 条。";
            allianceStatus = response.objective_satisfied
                ? "可信证据已改变联盟与剧情结局"
                : "玩家干预改变了 NPC 的自主传播链";
        }

        private void OnTurnCompleted(TurnResponse response)
        {
            interactionFeedback = "世界已经回应";
            interactionFeedbackUntil = Time.unscaledTime + 2.4f;
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
