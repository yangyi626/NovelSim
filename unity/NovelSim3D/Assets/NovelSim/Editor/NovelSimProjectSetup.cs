using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace NovelSim.Editor
{
    [InitializeOnLoad]
    public static class NovelSimProjectSetup
    {
        private const string SceneDirectory = "Assets/NovelSim/Scenes";
        private const string ScenePath =
            SceneDirectory + "/VerticalSlice.unity";
        private const string SessionKey = "NovelSim.VerticalSliceSetup";

        static NovelSimProjectSetup()
        {
            if (!SessionState.GetBool(SessionKey, false))
            {
                SessionState.SetBool(SessionKey, true);
                EditorApplication.delayCall += EnsureVerticalSlice;
            }
        }

        [MenuItem("NovelSim/Setup Vertical Slice")]
        public static void EnsureVerticalSlice()
        {
            Directory.CreateDirectory(SceneDirectory);
            if (!File.Exists(ScenePath))
            {
                var active = SceneManager.GetActiveScene();
                if (active.isDirty && active.rootCount > 0)
                {
                    Debug.LogWarning(
                        "当前场景有未保存内容；请保存后执行 "
                        + "NovelSim/Setup Vertical Slice。");
                    return;
                }
                var scene = EditorSceneManager.NewScene(
                    NewSceneSetup.EmptyScene,
                    NewSceneMode.Single);
                EditorSceneManager.SaveScene(scene, ScenePath);
            }

            var scenes = EditorBuildSettings.scenes.ToList();
            if (scenes.All(item => item.path != ScenePath))
            {
                scenes.Insert(0, new EditorBuildSettingsScene(
                    ScenePath,
                    true));
                EditorBuildSettings.scenes = scenes.ToArray();
            }

            PlayerSettings.companyName = "NovelSim";
            PlayerSettings.productName = "NovelSim 3D";
            PlayerSettings.defaultScreenWidth = 1280;
            PlayerSettings.defaultScreenHeight = 720;
            AssetDatabase.Refresh();
            Debug.Log(
                "NovelSim 3D 竖切片场景已就绪。运行前请启动 FastAPI。");
        }
    }
}
