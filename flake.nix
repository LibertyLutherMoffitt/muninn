{
  description = "Muninn — encrypted P2P chat over Bluetooth Classic";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = {
    self,
    nixpkgs,
    flake-utils,
  }:
    flake-utils.lib.eachDefaultSystem (
      system: let
        pkgs = nixpkgs.legacyPackages.${system};

        pythonPkgs = pkgs.python3.withPackages (ps: [
          ps.pynacl
          ps.dbus-python
          ps.pygobject3
        ]);

        muninn-linux = pkgs.python3Packages.buildPythonApplication {
          pname = "muninn";
          version = "0.1.0";
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
          version = "0.1.0";
          src = ./python;
          pyproject = true;

          build-system = [pkgs.python3Packages.setuptools];

          dependencies = [
            pkgs.python3Packages.pynacl
            pkgs.python3Packages.dbus-python
            pkgs.python3Packages.pygobject3
            pkgs.python3Packages.pyside6
          ];

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

            # Android client deps (future)
            pkgs.jdk17
            pkgs.kotlin
            pkgs.gradle
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
          '';
        };
      }
    );
}
