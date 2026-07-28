using System;
using System.Collections;
using System.Text;
using UnityEngine;
using UnityEngine.Networking;

namespace NovelSim.Network
{
    public interface INovelSimApiClient
    {
        IEnumerator StartSession(
            string packageId,
            Action<SessionResponse> onSuccess,
            Action<string> onFailure);

        IEnumerator ResumeSession(
            string sessionId,
            Action<SessionResponse> onSuccess,
            Action<string> onFailure);

        IEnumerator SubmitTurn(
            string sessionId,
            string text,
            Action<TurnResponse> onSuccess,
            Action<string> onFailure);
    }

    public sealed class NovelSimApiClient : MonoBehaviour, INovelSimApiClient
    {
        private const string DefaultBaseUrl = "http://127.0.0.1:8000";

        [SerializeField]
        private string baseUrl = DefaultBaseUrl;

        [SerializeField]
        private int timeoutSeconds = 90;

        public string BaseUrl => baseUrl;

        public void Configure(string value)
        {
            baseUrl = string.IsNullOrWhiteSpace(value)
                ? DefaultBaseUrl
                : value.Trim().TrimEnd('/');
        }

        public IEnumerator StartSession(
            string packageId,
            Action<SessionResponse> onSuccess,
            Action<string> onFailure)
        {
            var payload = new StartRequest
            {
                package_id = string.IsNullOrWhiteSpace(packageId)
                    ? "huarong_lane"
                    : packageId.Trim(),
                save_name = string.Empty,
            };
            yield return SendJson(
                UnityWebRequest.kHttpVerbPOST,
                ApiContractV1.StartSession,
                JsonUtility.ToJson(payload),
                onSuccess,
                onFailure);
        }

        public IEnumerator ResumeSession(
            string sessionId,
            Action<SessionResponse> onSuccess,
            Action<string> onFailure)
        {
            var path = ApiContractV1.ResumeSession
                + "?session="
                + UnityWebRequest.EscapeURL(sessionId ?? string.Empty);
            yield return SendJson<SessionResponse>(
                UnityWebRequest.kHttpVerbGET,
                path,
                null,
                onSuccess,
                onFailure);
        }

        public IEnumerator SubmitTurn(
            string sessionId,
            string text,
            Action<TurnResponse> onSuccess,
            Action<string> onFailure)
        {
            var payload = new TurnRequest
            {
                session_id = sessionId,
                text = text,
                use_npc_agents = true,
            };
            yield return SendJson(
                UnityWebRequest.kHttpVerbPOST,
                ApiContractV1.SubmitTurn,
                JsonUtility.ToJson(payload),
                onSuccess,
                onFailure);
        }

        private IEnumerator SendJson<T>(
            string method,
            string path,
            string body,
            Action<T> onSuccess,
            Action<string> onFailure)
            where T : ApiResponse
        {
            using (var request = new UnityWebRequest(BuildUrl(path), method))
            {
                request.downloadHandler = new DownloadHandlerBuffer();
                request.timeout = Mathf.Max(5, timeoutSeconds);
                request.SetRequestHeader("Accept", "application/json");
                if (body != null)
                {
                    request.uploadHandler = new UploadHandlerRaw(
                        Encoding.UTF8.GetBytes(body));
                    request.SetRequestHeader(
                        "Content-Type",
                        "application/json; charset=utf-8");
                }

                yield return request.SendWebRequest();

                var contractError = ValidateContractHeader(
                    request.GetResponseHeader("X-NovelSim-Contract"));
                if (!string.IsNullOrEmpty(contractError))
                {
                    onFailure?.Invoke(contractError);
                    yield break;
                }

                var raw = request.downloadHandler?.text ?? string.Empty;
                if (request.result != UnityWebRequest.Result.Success)
                {
                    onFailure?.Invoke(
                        $"HTTP {request.responseCode}: {ExtractError(raw, request.error)}");
                    yield break;
                }

                T response;
                try
                {
                    response = JsonUtility.FromJson<T>(raw);
                }
                catch (Exception exception)
                {
                    onFailure?.Invoke($"响应 JSON 无法解析: {exception.Message}");
                    yield break;
                }

                if (response == null)
                {
                    onFailure?.Invoke("服务端返回了空响应。");
                    yield break;
                }
                if (string.Equals(response.status, "error", StringComparison.OrdinalIgnoreCase))
                {
                    onFailure?.Invoke(string.IsNullOrEmpty(response.error)
                        ? "服务端返回错误。"
                        : response.error);
                    yield break;
                }
                onSuccess?.Invoke(response);
            }
        }

        private string BuildUrl(string path)
        {
            return baseUrl.TrimEnd('/') + "/" + path.TrimStart('/');
        }

        private static string ValidateContractHeader(string version)
        {
            if (string.IsNullOrWhiteSpace(version))
            {
                return "响应缺少 X-NovelSim-Contract，无法确认 API 兼容性。";
            }
            var major = version.Split('.')[0];
            return major == ApiContractV1.MajorVersion.ToString()
                ? string.Empty
                : $"API 主版本不兼容：客户端 v{ApiContractV1.Version}，服务端 v{version}。";
        }

        private static string ExtractError(string raw, string fallback)
        {
            try
            {
                var response = JsonUtility.FromJson<ApiResponse>(raw);
                if (!string.IsNullOrWhiteSpace(response?.error))
                {
                    return response.error;
                }
            }
            catch (Exception)
            {
                // 非 JSON 错误体使用 UnityWebRequest 自带错误。
            }
            return string.IsNullOrWhiteSpace(fallback) ? "请求失败" : fallback;
        }
    }
}
