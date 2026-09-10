import { CylinderGeometry, Group, Mesh, MeshPhysicalMaterial, TorusGeometry, SphereGeometry } from "three";
import { RoundedBoxGeometry } from "three/addons/geometries/RoundedBoxGeometry.js";
export const SURVEYOR = Object.freeze({width:18,depth:12.5,height:5.5,bodyWidth:8.1,rotorRadius:2.1,bodyY:3.3,rotorY:3.8,emitter:.55});
export const SURVEYOR_SPEC = "GT Surveyor v2: W18 x D12.5 x H5.5; 45% central capsule, four enclosed micro-rotors, integrated arms, tapered scanner, forward sensor and twin rear fins. One status emitter.";
export class SurveyorFactory {
  readonly body = new MeshPhysicalMaterial({color:0xb8c2cc,roughness:.42,metalness:.45});
  readonly dark = new MeshPhysicalMaterial({color:0x202a35,roughness:.48,metalness:.4});
  private box = new RoundedBoxGeometry(1,1,1,2,.08);
  private capsule = new SphereGeometry(1,24,12);
  private rotor = new TorusGeometry(1.7,.4,10,32);
  private sensor = new CylinderGeometry(2, .85, 1.8, 16);
  private aperture = new CylinderGeometry(.65,.65,.18,16);
  create(color:string): Group {
    const group=new Group();
    const indicator=new MeshPhysicalMaterial({color,emissive:color,emissiveIntensity:.25,roughness:.4,metalness:.3});
    group.userData.indicator=indicator;
    const box=(w:number,h:number,d:number,x:number,y:number,z:number,mat=this.body) => {
      const m=new Mesh(this.box,mat);m.scale.set(w,h,d);m.position.set(x,y,z);group.add(m);return m;
    };
    const body=new Mesh(this.capsule,this.body);body.scale.set(4.05,1.4,3.9);body.position.y=3.25;group.add(body);
    for(const x of [-6.9,6.9]) for(const z of [-4.15,4.15]) {
      const arm=box(5,.65,1.15,x/1.65,3.65,z/1.4);arm.rotation.y=-Math.sign(x*z)*.48;
      const ring=new Mesh(this.rotor,this.body);ring.rotation.x=Math.PI/2;ring.position.set(x,3.65,z);group.add(ring);
      box(3,.12,.35,x,3.65,z,this.dark).userData.rotorBlade=true;
    }
    const sensor=new Mesh(this.sensor,this.dark);sensor.position.y=1;group.add(sensor);
    const aperture=new Mesh(this.aperture,this.dark);aperture.position.y=.09;group.add(aperture);
    box(.8,.65,.4,0,3,-3.95,this.dark);
    for(const x of [-2.3,2.3]) {box(.4,1.6,2.2,x,4.7,2.9);}
    box(.55,.55,.2,0,4,-3.7,indicator);
    return group;
  }
  release(group:Group) {(group.userData.indicator as MeshPhysicalMaterial).dispose();group.clear();}
  dispose() {this.body.dispose();this.dark.dispose();this.box.dispose();this.capsule.dispose();this.rotor.dispose();this.sensor.dispose();this.aperture.dispose();}
}
