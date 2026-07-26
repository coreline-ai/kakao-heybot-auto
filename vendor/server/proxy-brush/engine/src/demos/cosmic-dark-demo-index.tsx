import React from "react";
import {Composition, registerRoot} from "remotion";
import {CosmicDarkBrushDemo} from "./CosmicDarkBrushDemo";

const CosmicDarkDemoRoot: React.FC = () => <Composition
  id="CosmicDarkBrushDemo"
  component={CosmicDarkBrushDemo}
  width={1920}
  height={1080}
  fps={30}
  durationInFrames={300}
/>;

registerRoot(CosmicDarkDemoRoot);
