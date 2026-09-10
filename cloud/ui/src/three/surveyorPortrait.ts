import { ACESFilmicToneMapping, DirectionalLight, HemisphereLight, PerspectiveCamera, Scene, SRGBColorSpace, WebGLRenderer } from "three";
import { SurveyorFactory } from "./surveyor";

/** One render of the same procedural vehicle used in the repository. */
export function drawSurveyorPortrait(canvas:HTMLCanvasElement,color:string,dark:boolean) {
  const renderer=new WebGLRenderer({canvas,alpha:true,antialias:true});
  renderer.setPixelRatio(Math.min(window.devicePixelRatio||1,2));
  renderer.setSize(160,110,false);renderer.outputColorSpace=SRGBColorSpace;
  renderer.toneMapping=ACESFilmicToneMapping;renderer.toneMappingExposure=1.1;
  const scene=new Scene(),camera=new PerspectiveCamera(30,160/110,.1,300);
  camera.position.set(20,20,34);camera.lookAt(0,3,0);
  const factory=new SurveyorFactory();factory.body.color.set(dark?"#9aa8ba":"#b2c0d1");
  const vehicle=factory.create(color);scene.add(vehicle,new HemisphereLight(0xffffff,0x718398,2));
  const key=new DirectionalLight(0xffffff,3);key.position.set(-10,25,20);scene.add(key);
  renderer.render(scene,camera);
  return ()=>{factory.release(vehicle);factory.dispose();scene.clear();renderer.dispose();renderer.forceContextLoss();};
}
