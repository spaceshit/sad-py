# Pinned upstream references

`manifest.json` records immutable commits, original source paths, and SHA-256 hashes. Each repository's license is stored alongside its files. These sources are data only: tests neither install their dependencies nor execute JavaScript. Regeneration is an explicit maintainer operation, never a test or analysis side effect.

The executable detection fixtures in `../adapted` are reduced, modified examples:

- Axios: README “Creating an instance”, “Custom instance defaults”, and configuration precedence. Added explicit requests and example.test domains to make the expected URLs independently checkable.
- OpenAPI Generator (Apache-2.0): `BaseAPI.request` and `createFetchParams` structure. Removed middleware/response validation and supplied a small derived PetAPI. This is not a claim to support the complete upstream runtime.
- tRPC (MIT): minimal-react client transport configuration. React query hooks replaced with explicit proxy calls; the additional mutation is synthetic.
- Connect (Apache-2.0): Vite example's transport and `say`/`introduce` calls. Replaced encoded generated descriptors with a literal descriptor verified against the pinned generated RPC declarations. The unchanged App.tsx is also analyzed with this descriptor supplied as a companion input.

Modified excerpts retain their upstream license through these adjacent license files and this attribution. Application endpoints in these files are never contacted.
