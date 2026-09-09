package main

type Runner interface {
	Run()
}

type ImplA struct{}

func (ImplA) Run() {}

type ImplB struct{}

func (ImplB) Run() {}

func main() {
	var runner Runner = ImplA{}
	runner = ImplB{}
	runner.Run()
}
