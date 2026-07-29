using System.Collections.Generic;
using UnityEngine;
using UnityEngine.AI;

namespace NovelSim.World
{
    /// <summary>
    /// Bakes the small vertical-slice lane at runtime. This keeps the generated
    /// scene testable and removes the need for a checked-in editor bake.
    /// </summary>
    public sealed class RuntimeLaneNavMesh : MonoBehaviour
    {
        private NavMeshData navMeshData;
        private NavMeshDataInstance navMeshInstance;

        public bool IsReady =>
            navMeshData != null && navMeshInstance.valid;

        public int SourceCount { get; private set; }

        public bool Build(Transform environmentRoot)
        {
            Remove();
            if (environmentRoot == null)
            {
                return false;
            }

            var sources = new List<NavMeshBuildSource>();
            NavMeshBuilder.CollectSources(
                environmentRoot,
                ~0,
                NavMeshCollectGeometry.PhysicsColliders,
                0,
                new List<NavMeshBuildMarkup>(),
                sources);
            SourceCount = sources.Count;
            if (SourceCount == 0)
            {
                return false;
            }

            var settings = NavMesh.GetSettingsCount() > 0
                ? NavMesh.GetSettingsByIndex(0)
                : NavMesh.CreateSettings();
            settings.agentRadius = 0.32f;
            settings.agentHeight = 2.1f;
            settings.agentClimb = 0.32f;
            settings.agentSlope = 42f;
            settings.overrideVoxelSize = true;
            settings.voxelSize = 0.12f;
            settings.overrideTileSize = true;
            settings.tileSize = 128;

            var bounds = new Bounds(
                environmentRoot.position + new Vector3(0f, 2.5f, 4f),
                new Vector3(11f, 7f, 35f));
            navMeshData = NavMeshBuilder.BuildNavMeshData(
                settings,
                sources,
                bounds,
                Vector3.zero,
                Quaternion.identity);
            if (navMeshData == null)
            {
                return false;
            }
            navMeshData.name = "Huarong Lane Runtime NavMesh";
            navMeshInstance = NavMesh.AddNavMeshData(navMeshData);
            return navMeshInstance.valid;
        }

        private void OnDestroy()
        {
            Remove();
        }

        private void Remove()
        {
            if (navMeshInstance.valid)
            {
                navMeshInstance.Remove();
            }
            navMeshData = null;
            SourceCount = 0;
        }
    }
}
