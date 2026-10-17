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

        # Separate SDK with emulator + system image so the default shell
        # doesn't pull ~2 GB of images. Used by `nix run .#android-emu`.
        androidEmuSdk =
          (pkgs.androidenv.composeAndroidPackages {
            platformVersions = ["34"];
            abiVersions = ["x86_64"];
            includeEmulator = true;
            includeSystemImages = true;
            systemImageTypes = ["google_apis"];
            includeNDK = false;
            includeSources = false;
          }).androidsdk;

        # Headless KVM emulator. AVD lives in $XDG_DATA_HOME/muninn-avd so it
        # survives restarts. Drive it with adb (screencap / input). Set
        # MUNINN_AVD=b (etc.) for extra independent devices on netsim BT.
        android-emu = pkgs.writeShellApplication {
          name = "android-emu";
          runtimeInputs = [androidEmuSdk];
          text = ''
            export ANDROID_SDK_ROOT="${androidEmuSdk}/libexec/android-sdk"
            export ANDROID_HOME="$ANDROID_SDK_ROOT"
            export ANDROID_USER_HOME="''${XDG_DATA_HOME:-$HOME/.local/share}/muninn-avd"
            export ANDROID_AVD_HOME="$ANDROID_USER_HOME/avd"
            mkdir -p "$ANDROID_AVD_HOME"
            name="muninn''${MUNINN_AVD:+-$MUNINN_AVD}"
            if [ ! -d "$ANDROID_AVD_HOME/$name.avd" ]; then
              echo no | avdmanager create avd -n "$name" -d pixel_6 \
                -k "system-images;android-34;google_apis;x86_64" --force
              printf 'hw.keyboard=yes\nhw.ramSize=4096\n' >> "$ANDROID_AVD_HOME/$name.avd/config.ini"
            fi
            exec emulator -avd "$name" -no-window -no-audio -no-boot-anim \
              -gpu swiftshader_indirect -no-snapshot-save "$@"
          '';
        };

        muninn-linux = pkgs.python3Packages.buildPythonApplication {
          pname = "muninn";
          version = "0.4.0";
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
          ps.pytest
          ps.pytest-timeout
          ps.pillow
        ]);

        muninn-gui = pkgs.python3Packages.buildPythonApplication {
          pname = "muninn-gui";
          version = "0.4.0";
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

        # Muninn GUI on a private Xvfb display for headless testing over ssh.
        # Drive with `DISPLAY=:98 xdotool ...`, screenshot with
        # `DISPLAY=:98 import -window root out.png`. Software Qt Quick backend
        # because Xvfb has no usable GLX. Uses a throwaway data dir so the real
        # identity DB isn't touched (override with MUNINN_TEST_DATA).
        gui-headless = pkgs.writeShellApplication {
          name = "gui-headless";
          runtimeInputs = [pkgs.xvfb muninn-gui];
          text = ''
            display="''${MUNINN_DISPLAY:-:98}"
            data="''${MUNINN_TEST_DATA:-$(mktemp -d)}"
            Xvfb "$display" -screen 0 1280x800x24 >/dev/null 2>&1 &
            xvfb=$!
            trap 'kill $xvfb 2>/dev/null' EXIT
            sleep 1
            echo "DISPLAY=$display data=$data" >&2
            DISPLAY="$display" QT_QPA_PLATFORM=xcb QT_QUICK_BACKEND=software \
              XDG_DATA_HOME="$data" MUNINN_NAME="''${MUNINN_NAME:-headless}" \
              muninn-gui "$@"
          '';
        };
      in {
        packages = {
          default = muninn-linux;
          muninn-linux = muninn-linux;
          muninn-gui = muninn-gui;
          android-emu = android-emu;
          gui-headless = gui-headless;
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

            # Headless GUI testing (xvfb-run + xdotool + screenshots)
            pkgs.xvfb
            pkgs.xvfb-run
            pkgs.xdotool
            pkgs.tmux
            pkgs.imagemagick

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

            export NIXPKGS_QT6_QML_IMPORT_PATH="${pkgs.qt6.qtdeclarative}/lib/qt-6/qml"
            export QT_PLUGIN_PATH="${pkgs.qt6.qtbase}/lib/qt-6/plugins:${pkgs.qt6.qtsvg}/lib/qt-6/plugins:${pkgs.qt6.qtdeclarative}/lib/qt-6/plugins:${pkgs.qt6.qtwayland}/lib/qt-6/plugins"
            export ANDROID_HOME="${androidSdkRoot}"
            export ANDROID_SDK_ROOT="${androidSdkRoot}"
            export JAVA_HOME="${pkgs.jdk17}"
            export GRADLE_OPTS="-Dorg.gradle.project.android.aapt2FromMavenOverride=${androidSdkRoot}/build-tools/34.0.0/aapt2"
          '';
        };
      }
    );
}
