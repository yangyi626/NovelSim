using System;
using System.Collections;
using UnityEngine;
using NovelSim.Network;
using NovelSim.UI;

namespace NovelSim.World
{
    /// <summary>
    /// Consumes the server-projected presentation stream exactly once per
    /// client cursor. Authoritative state remains on the server.
    /// </summary>
    public sealed class ToolEventDispatcher : MonoBehaviour
    {
        private const string CursorKeyPrefix =
            "NovelSim.PresentationCursor.";

        private WorldSessionManager session;
        private INovelSimApiClient api;
        private WorldEntityRegistry registry;
        private NovelSimHud hud;
        private bool syncing;
        private bool syncRequested;
        private string activeSessionId = string.Empty;

        public long LastAcknowledgedSequence { get; private set; }
        public int DispatchedCommandCount { get; private set; }
        public bool IsSyncing => syncing;

        public void Configure(
            WorldSessionManager sessionManager,
            INovelSimApiClient apiClient,
            WorldEntityRegistry entityRegistry,
            NovelSimHud targetHud)
        {
            session = sessionManager;
            api = apiClient;
            registry = entityRegistry;
            hud = targetHud;
            if (session != null)
            {
                session.SessionChanged += OnSessionChanged;
                session.TurnCompleted += OnTurnCompleted;
            }
        }

        private void OnDestroy()
        {
            if (session == null)
            {
                return;
            }
            session.SessionChanged -= OnSessionChanged;
            session.TurnCompleted -= OnTurnCompleted;
        }

        private void OnSessionChanged(SessionResponse response)
        {
            activeSessionId = response?.session_id ?? string.Empty;
            LastAcknowledgedSequence = LoadCursor(activeSessionId);
            syncRequested = false;
            StartCoroutine(RecoverSnapshot());
        }

        private void OnTurnCompleted(TurnResponse response)
        {
            syncRequested = true;
            if (!syncing)
            {
                StartCoroutine(SyncPendingCommands());
            }
        }

        public void ApplySnapshot(PresentationSnapshotDto snapshot)
        {
            if (snapshot == null)
            {
                return;
            }
            registry?.Reconcile(snapshot);
            hud?.SetAllianceStatus(
                snapshot.alliances != null
                && snapshot.alliances.Length > 0
                    ? $"联盟已同步：{snapshot.alliances[0].goal_key}"
                    : string.Empty);
            LastAcknowledgedSequence = Math.Max(
                0L,
                snapshot.last_sequence);
            SaveCursor();
        }

        public bool Consume(PresentationCommandDto command)
        {
            if (
                command == null
                || command.sequence <= LastAcknowledgedSequence)
            {
                return false;
            }
            Dispatch(command);
            LastAcknowledgedSequence = command.sequence;
            DispatchedCommandCount++;
            SaveCursor();
            return true;
        }

        private IEnumerator RecoverSnapshot()
        {
            if (
                syncing
                || api == null
                || string.IsNullOrWhiteSpace(activeSessionId))
            {
                yield break;
            }
            syncing = true;
            PresentationSnapshotResponse response = null;
            string failure = null;
            yield return api.FetchPresentationSnapshot(
                activeSessionId,
                value => response = value,
                message => failure = message);
            if (response?.snapshot != null)
            {
                ApplySnapshot(response.snapshot);
                hud?.ShowPresentationMessage(
                    $"表现状态已恢复至 v{response.snapshot.state_version}");
            }
            else if (!string.IsNullOrWhiteSpace(failure))
            {
                hud?.ShowPresentationMessage(
                    $"表现快照恢复失败：{failure}");
            }
            syncing = false;
            if (syncRequested)
            {
                StartCoroutine(SyncPendingCommands());
            }
        }

        private IEnumerator SyncPendingCommands()
        {
            if (
                syncing
                || api == null
                || string.IsNullOrWhiteSpace(activeSessionId))
            {
                yield break;
            }
            syncing = true;
            syncRequested = false;
            var fetchMore = true;
            while (fetchMore)
            {
                PresentationEventsResponse response = null;
                string failure = null;
                yield return api.FetchPresentationEvents(
                    activeSessionId,
                    LastAcknowledgedSequence,
                    value => response = value,
                    message => failure = message);
                if (!string.IsNullOrWhiteSpace(failure))
                {
                    syncing = false;
                    if (failure.StartsWith(
                        "HTTP 409:",
                        StringComparison.OrdinalIgnoreCase))
                    {
                        StartCoroutine(RecoverSnapshot());
                    }
                    else
                    {
                        hud?.ShowPresentationMessage(
                            $"表现事件同步失败：{failure}");
                    }
                    yield break;
                }
                var commands = response?.commands
                    ?? Array.Empty<PresentationCommandDto>();
                Array.Sort(
                    commands,
                    (left, right) => left.sequence.CompareTo(right.sequence));
                foreach (var command in commands)
                {
                    Consume(command);
                }
                fetchMore = response != null && response.has_more;
                if (fetchMore && commands.Length == 0)
                {
                    hud?.ShowPresentationMessage(
                        "表现事件分页无进展，已停止同步。");
                    fetchMore = false;
                }
            }
            syncing = false;
            if (syncRequested)
            {
                StartCoroutine(SyncPendingCommands());
            }
        }

        private void Dispatch(PresentationCommandDto command)
        {
            switch (command.command_type ?? string.Empty)
            {
                case "navigate":
                    if (
                        registry == null
                        || !registry.Navigate(
                        command.actor_id,
                        command.location_id))
                    {
                        hud?.ShowPresentationMessage(
                            $"导航目标暂未加载：{command.location_id}");
                    }
                    break;
                case "dialogue":
                    registry?.Face(command.actor_id, command.target_id);
                    hud?.ShowPresentationMessage(
                        string.IsNullOrWhiteSpace(command.text)
                            ? $"{command.actor_id} 正在交谈"
                            : command.text);
                    break;
                case "information_shared":
                    registry?.Face(command.actor_id, command.target_id);
                    hud?.ShowPresentationMessage(
                        $"信息已传播：{command.fact_id}");
                    break;
                case "item_picked_up":
                case "item_given":
                case "item_transferred":
                case "item_destroyed":
                    registry?.SetItemUnavailable(command.entity_id);
                    hud?.ShowPresentationMessage(
                        ItemMessage(command));
                    break;
                case "fact_observed":
                case "knowledge_updated":
                    hud?.ShowPresentationMessage(
                        $"获得事实：{command.fact_id}");
                    break;
                case "alliance_formed":
                    hud?.SetAllianceStatus(
                        $"联盟成立：{command.alliance_id}");
                    hud?.ShowPresentationMessage("新的联盟已经成立。");
                    break;
                case "system_hint":
                    hud?.ShowPresentationMessage(command.text);
                    break;
                default:
                    Debug.Log(
                        $"NovelSim ignored additive presentation command "
                        + $"{command.command_type} ({command.command_id}).");
                    break;
            }
        }

        private static string ItemMessage(PresentationCommandDto command)
        {
            switch (command.command_type)
            {
                case "item_destroyed":
                    return $"物品已销毁：{command.entity_id}";
                case "item_given":
                case "item_transferred":
                    return $"物品所有权已更新：{command.entity_id}";
                default:
                    return $"获得物品：{command.entity_id}";
            }
        }

        private long LoadCursor(string sessionId)
        {
            if (string.IsNullOrWhiteSpace(sessionId))
            {
                return 0L;
            }
            var raw = PlayerPrefs.GetString(
                CursorKeyPrefix + sessionId,
                "0");
            return long.TryParse(raw, out var value)
                ? Math.Max(0L, value)
                : 0L;
        }

        private void SaveCursor()
        {
            if (string.IsNullOrWhiteSpace(activeSessionId))
            {
                return;
            }
            PlayerPrefs.SetString(
                CursorKeyPrefix + activeSessionId,
                LastAcknowledgedSequence.ToString());
            PlayerPrefs.Save();
        }
    }
}
