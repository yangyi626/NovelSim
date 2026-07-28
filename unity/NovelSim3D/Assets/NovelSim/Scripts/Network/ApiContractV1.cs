namespace NovelSim.Network
{
    /// <summary>
    /// Unity 客户端消费的稳定 API v1 子集。
    /// Python 契约测试会把这些常量与 contracts/api-v1.json 对齐。
    /// </summary>
    public static class ApiContractV1
    {
        public const string Version = "1.0.0";
        public const int MajorVersion = 1;

        public const string Metadata = "/api/meta/contract";
        public const string StartSession = "/api/start";
        public const string ResumeSession = "/api/session";
        public const string SubmitTurn = "/api/turn";
        public const string State = "/api/state";
        public const string Events = "/api/events";
    }
}
