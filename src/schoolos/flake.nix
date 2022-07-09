{
  inputs = {
  };

  outputs = { self }: {
    nixosModules = [
      ./server
    ];
  };
}

