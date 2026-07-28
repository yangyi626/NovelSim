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
        [SerializeField]
        private string packageId = "huarong_lane";

        private NovelSimApiClient api;

        public string SessionId { get; private set; }
        public WorldStateDto State { get; private set; }
        public bool Busy { get; private set; }

        public event Action<string> StatusChanged;
        public event Action<string> ErrorRaised;
        public event Action<SessionResponse> SessionChanged;
        public event Action<TurnResponse> TurnCompleted;

        public void Configure(NovelSimApiClient apiClient)
        {
            api = apiClient;
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
                    SessionId = response.session_id;
                    State = response.state;
                    SetBusy(false, $"已进入 {response.world_meta?.scenario ?? "NovelSim"}");
                    SessionChanged?.Invoke(response);
                },
                Fail));
        }

        public void ResumeSession(string sessionId)
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
                    SessionId = response.session_id;
                    State = response.state;
                    SetBusy(false, $"世界线已恢复至 v{State?.version ?? 0}");
                    SessionChanged?.Invoke(response);
                },
                Fail));
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
            ErrorRaised?.Invoke(message);
            StatusChanged?.Invoke("请求失败，可修改服务地址后重试。");
        }

        private void SetBusy(bool value, string message)
        {
            Busy = value;
            StatusChanged?.Invoke(message);
        }
    }
}
