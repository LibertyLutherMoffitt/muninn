{
  description = "Muninn — encrypted P2P chat over Bluetooth Classic";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = {
    nixpkgs,
    flake-utils,
    ...
  }:
    flake-utils.lib.eachDefaultSystem (
      system: let
        pkgs = import nixpkgs {
          inherit system;
          config = {
            allowUnfree = true;
            android_sdk.accept_license = true;
          };
        };

        androidComposition = pkgs.androidenv.composeAndroidPackages {
          platformVersions = ["34"];
          buildToolsVersions = ["34.0.0"];
          abiVersions = ["x86_64" "arm64-v8a"];
          includeNDK = false;
          includeEmulator = false;
          includeSystemImages = false;
          includeSources = false;
        };
        androidSdk = androidComposition.androidsdk;
        androidSdkRoot = "${androidSdk}/libexec/android-sdk";

        muninn-linux = pkgs.python3Packages.buildPythonApplication {
          pname = "muninn";
          version = "0.1.11";
          src = ./python;
          pyproject = true;

          build-system = [pkgs.python3Packages.setuptools];

          dependencies = [
            pkgs.python3Packages.pynacl
            pkgs.python3Packages.dbus-python
            pkgs.python3Packages.pygobject3
          ];

          meta = {
            description = "Muninn Linux CLI — encrypted P2P chat over Bluetooth";
            mainProgram = "muninn";
          };
        };
        pythonGuiPkgs = pkgs.python3.withPackages (ps: [
          ps.pynacl
          ps.dbus-python
          ps.pygobject3
          ps.pyside6
        ]);

        muninn-gui = pkgs.python3Packages.buildPythonApplication {
          pname = "muninn-gui";
          version = "0.1.11";
          src = ./python;
          pyproject = true;

          nativeBuildInputs = [
            pkgs.qt6.wrapQtAppsHook
          ];

          build-system = [pkgs.python3Packages.setuptools];

          dependencies = [
            pkgs.python3Packages.pynacl
            pkgs.python3Packages.dbus-python
            pkgs.python3Packages.pygobject3
            pkgs.python3Packages.pyside6
          ];

          buildInputs = [
            pkgs.qt6.qtbase
            pkgs.qt6.qtdeclarative
            pkgs.qt6.qtsvg
          ];

          dontWrapQtApps = true;

          preFixup = ''
            makeWrapperArgs+=("''${qtWrapperArgs[@]}")
          '';

          meta = {
            description = "Muninn GUI — encrypted P2P chat over Bluetooth";
            mainProgram = "muninn-gui";
          };
        };
      in {
        packages = {
          default = muninn-linux;
          muninn-linux = muninn-linux;
          muninn-gui = muninn-gui;
          cli = muninn-linux;
          gui = muninn-gui;
        };

        devShells.default = pkgs.mkShell {
          packages = [
            pythonGuiPkgs
            pkgs.bluez
            pkgs.pkg-config
            pkgs.qt6.qtbase
            pkgs.qt6.qtdeclarative
            pkgs.qt6.qtwayland

            # Tooling
            pkgs.ruff
            pkgs.ty
            pkgs.prek
            pkgs.alejandra

            # Android client
            pkgs.jdk17
            pkgs.kotlin
            pkgs.gradle
            pkgs.android-tools
            androidSdk
          ];

          shellHook = ''
            if [ ! -f .git/hooks/pre-commit ] || ! grep -q 'nix develop' .git/hooks/pre-commit 2>/dev/null; then
              mkdir -p .git/hooks
              cat > .git/hooks/pre-commit << 'HOOK'
            #!/usr/bin/env bash
            exec nix develop --command prek run --hook-stage pre-commit
            HOOK
              chmod +x .git/hooks/pre-commit
            fi

            export ANDROID_HOME="${androidSdkRoot}"
            export ANDROID_SDK_ROOT="${androidSdkRoot}"
            export JAVA_HOME="${pkgs.jdk17}"
            export GRADLE_OPTS="-Dorg.gradle.project.android.aapt2FromMavenOverride=${androidSdkRoot}/build-tools/34.0.0/aapt2"
          '';
        };
      }
    );
}
