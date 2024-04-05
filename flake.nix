# copy flake.nix, flake.lock, and elements.nix to the git root of the repository
{
  description = "Elements is a fork of Bitcoin Core, with advanced blockchain features extending the Bitcoin protocol";

  inputs = {
    nixpkgs.url = "nixpkgs/release-23.05";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        boost = pkgs.boost175;
        doCheck = false;
        withGui = true;
      in
      {
        packages = {
          default =
            pkgs.libsForQt5.callPackage ./elements.nix {
              inherit boost;
              inherit doCheck;
              inherit withGui;
            };
        };

        # this is not working yet
        devShells.default = pkgs.mkShell {
          nativeBuildInputs = with pkgs; [ autoreconfHook pkg-config util-linux ];

          buildInputs = with pkgs; [ boost libevent miniupnpc zeromq zlib db48 sqlite ];

          shellHook = ''
            echo "Elements dev shell"
            export BOOST_LIBDIR=${boost.out}/lib
          '';
        };
      }
    );
}

