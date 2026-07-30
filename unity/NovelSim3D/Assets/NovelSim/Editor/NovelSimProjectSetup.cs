using System.IO;
using System.Linq;
using System;
using UnityEditor;
using UnityEditor.Build;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;
using UnityEngine.SceneManagement;

namespace NovelSim.Editor
{
    [InitializeOnLoad]
    public static class NovelSimProjectSetup
    {
        private const string SceneDirectory = "Assets/NovelSim/Scenes";
        private const string ScenePath =
            SceneDirectory + "/VerticalSlice.unity";
        private const string RenderingDirectory =
            "Assets/NovelSim/Rendering";
        private const string RendererPath =
            RenderingDirectory + "/NovelSimUniversalRenderer.asset";
        private const string PipelinePath =
            RenderingDirectory + "/NovelSimUniversalPipeline.asset";
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
            EnsureRendering();
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
            PlayerSettings.productName = "NovelSim Agent Showcase";
            PlayerSettings.defaultScreenWidth = 1280;
            PlayerSettings.defaultScreenHeight = 720;
            PlayerSettings.colorSpace = ColorSpace.Linear;
            AssetDatabase.Refresh();
            AssetDatabase.SaveAssets();
            Debug.Log(
                "NovelSim 3D 竖切片场景已就绪。运行前请启动 FastAPI。");
        }

        [MenuItem("NovelSim/Open Vertical Slice")]
        public static void OpenVerticalSlice()
        {
            EnsureVerticalSlice();
            EditorSceneManager.OpenScene(
                ScenePath,
                OpenSceneMode.Single);
        }

        [MenuItem("NovelSim/Play Vertical Slice")]
        public static void PlayVerticalSlice()
        {
            OpenVerticalSlice();
            EditorApplication.isPlaying = true;
            EditorApplication.delayCall += FocusGameView;
        }

        private static void FocusGameView()
        {
            var gameViewType = typeof(EditorWindow).Assembly.GetType(
                "UnityEditor.GameView");
            if (gameViewType == null)
            {
                return;
            }
            var gameView = EditorWindow.GetWindow(gameViewType);
            gameView.Show();
            gameView.Focus();
        }

        private static void EnsureRendering()
        {
            Directory.CreateDirectory(RenderingDirectory);

            var renderer = AssetDatabase.LoadAssetAtPath<
                UniversalRendererData>(RendererPath);
            if (renderer == null)
            {
                renderer = ScriptableObject.CreateInstance<
                    UniversalRendererData>();
                renderer.name = "NovelSim Universal Renderer";
                AssetDatabase.CreateAsset(renderer, RendererPath);
            }

            var pipeline = AssetDatabase.LoadAssetAtPath<
                UniversalRenderPipelineAsset>(PipelinePath);
            if (pipeline == null)
            {
                pipeline = UniversalRenderPipelineAsset.Create(renderer);
                pipeline.name = "NovelSim Universal Pipeline";
                pipeline.renderScale = 1f;
                pipeline.shadowDistance = 60f;
                AssetDatabase.CreateAsset(pipeline, PipelinePath);
            }

            GraphicsSettings.defaultRenderPipeline = pipeline;
            QualitySettings.renderPipeline = pipeline;
            EditorUtility.SetDirty(pipeline);
        }
    }

    public static class NovelSimWindowsBuild
    {
        private const string DefaultOutput =
            "Builds/Windows/NovelSim3D.exe";

        [MenuItem("NovelSim/Build Windows x64")]
        public static void BuildWindows()
        {
            NovelSimProjectSetup.EnsureVerticalSlice();
            var configured = Environment.GetEnvironmentVariable(
                "NOVELSIM_WINDOWS_BUILD_PATH");
            var outputPath = Path.GetFullPath(
                string.IsNullOrWhiteSpace(configured)
                    ? DefaultOutput
                    : configured);
            var directory = Path.GetDirectoryName(outputPath);
            if (!string.IsNullOrWhiteSpace(directory))
            {
                Directory.CreateDirectory(directory);
            }
            var scenes = EditorBuildSettings.scenes
                .Where(item => item.enabled)
                .Select(item => item.path)
                .ToArray();
            if (scenes.Length == 0)
            {
                throw new BuildFailedException(
                    "没有启用的 Unity 构建场景。");
            }

            PlayerSettings.SetApplicationIdentifier(
                NamedBuildTarget.Standalone,
                "com.novelsim.verticalslice");
            var report = BuildPipeline.BuildPlayer(
                new BuildPlayerOptions
                {
                    scenes = scenes,
                    locationPathName = outputPath,
                    target = BuildTarget.StandaloneWindows64,
                    options = BuildOptions.None,
                });
            if (report.summary.result != BuildResult.Succeeded)
            {
                throw new BuildFailedException(
                    $"Windows 构建失败：{report.summary.result}");
            }
            Debug.Log(
                $"NOVELSIM_WINDOWS_BUILD_OK path={outputPath} "
                + $"bytes={report.summary.totalSize}");
        }

        public static void BuildWindowsFromCommandLine()
        {
            BuildWindows();
        }
    }
}
