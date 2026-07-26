import React from "react";
import {Composition, registerRoot} from "remotion";
import {CosmicRandomBrushDemo} from "./CosmicRandomBrushDemo";

const Root: React.FC = () => <Composition
  id="CosmicRandomBrushDemo"
  component={CosmicRandomBrushDemo}
  width={1920}
  height={1080}
  fps={30}
  durationInFrames={300}
/>;

registerRoot(Root);
