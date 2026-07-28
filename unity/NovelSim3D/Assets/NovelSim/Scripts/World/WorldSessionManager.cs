using System;
using UnityEngine;
using NovelSim.Network;

namespace NovelSim.World
{
    /// <summary>
    /// 保存客户端只读镜像；权威世界状态始终来自 FastAPI/SQLite。
    /// </summary>
    public sealed class WorldSessionManager : MonoBehaviour
    {
        public const string LastSessionKey = "NovelSim.LastSessionId";

        [SerializeField]
        private string packageId = "huarong_lane";

        private INovelSimApiClient api;

        public string SessionId { get; private set; }
        public WorldStateDto State { get; private set; }
        public bool Busy { get; private set; }
        public string LastError { get; private set; }
        public TurnResponse LastTurn { get; private set; }
        public bool HasSession => !string.IsNullOrWhiteSpace(SessionId);

        public event Action<string> StatusChanged;
        public event Action<string> ErrorRaised;
        public event Action<SessionResponse> SessionChanged;
        public event Action<TurnResponse> TurnCompleted;

        public void Configure(INovelSimApiClient apiClient)
        {
            api = apiClient;
        }

        public void ResumeLastOrStart()
        {
            var storedSession = PlayerPrefs.GetString(
                LastSessionKey,
                string.Empty);
            if (string.IsNullOrWhiteSpace(storedSession))
            {
                StartNewSession();
                return;
            }
            ResumeSessionInternal(storedSession, true);
        }

        public void StartNewSession()
        {
            if (!CanRequest())
            {
                return;
            }
            SetBusy(true, "正在进入世界……");
            StartCoroutine(api.StartSession(
                packageId,
                response =>
                {
                    AcceptSession(response);
                    SetBusy(false, $"已进入 {response.world_meta?.scenario ?? "NovelSim"}");
                    SessionChanged?.Invoke(response);
                },
                Fail));
        }

        public void ResumeSession(string sessionId)
        {
            ResumeSessionInternal(sessionId, false);
        }

        public static void ClearSavedSession()
        {
            PlayerPrefs.DeleteKey(LastSessionKey);
            PlayerPrefs.Save();
        }

        private void ResumeSessionInternal(
            string sessionId,
            bool startIfMissing)
        {
            if (!CanRequest() || string.IsNullOrWhiteSpace(sessionId))
            {
                return;
            }
            SetBusy(true, "正在恢复世界线……");
            StartCoroutine(api.ResumeSession(
                sessionId,
                response =>
                {
                    AcceptSession(response);
                    SetBusy(false, $"世界线已恢复至 v{State?.version ?? 0}");
                    SessionChanged?.Invoke(response);
                },
                message =>
                {
                    if (startIfMissing && IsMissingSession(message))
                    {
                        Busy = false;
                        ClearSavedSession();
                        StatusChanged?.Invoke(
                            "本地存档已失效，正在创建新世界线……");
                        StartNewSession();
                        return;
                    }
                    Fail(message);
                }));
        }

        public void SubmitAction(string text)
        {
            if (!CanRequest())
            {
                return;
            }
            if (string.IsNullOrWhiteSpace(SessionId))
            {
                ErrorRaised?.Invoke("尚未进入世界，请先新建世界线。");
                return;
            }
            if (string.IsNullOrWhiteSpace(text))
            {
                ErrorRaised?.Invoke("行动内容不能为空。");
                return;
            }

            SetBusy(true, "世界正在推演……");
            StartCoroutine(api.SubmitTurn(
                SessionId,
                text.Trim(),
                response =>
                {
                    if (response.state != null)
                    {
                        State = response.state;
                    }
                    LastTurn = response;
                    LastError = string.Empty;
                    SetBusy(false, $"世界线已推进至 v{State?.version ?? 0}");
                    TurnCompleted?.Invoke(response);
                },
                Fail));
        }

        private bool CanRequest()
        {
            if (api == null)
            {
                ErrorRaised?.Invoke("NovelSim API 客户端尚未配置。");
                return false;
            }
            return !Busy;
        }

        private void Fail(string message)
        {
            Busy = false;
            LastError = message ?? "请求失败";
            ErrorRaised?.Invoke(message);
            StatusChanged?.Invoke("请求失败，可修改服务地址后重试。");
        }

        private void AcceptSession(SessionResponse response)
        {
            SessionId = response.session_id;
            State = response.state;
            LastTurn = null;
            LastError = string.Empty;
            PlayerPrefs.SetString(LastSessionKey, SessionId);
            PlayerPrefs.Save();
        }

        private static bool IsMissingSession(string message)
        {
            return !string.IsNullOrWhiteSpace(message)
                && message.StartsWith(
                    "HTTP 404:",
                    StringComparison.OrdinalIgnoreCase);
        }

        private void SetBusy(bool value, string message)
        {
            Busy = value;
            StatusChanged?.Invoke(message);
        }
    }
}
