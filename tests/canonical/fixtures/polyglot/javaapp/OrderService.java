package javaapp;

@Service
public class OrderService implements Pricing {

    private OrderRepository repository;

    public String greet(String name) {
        return this.prefix() + " " + name;
    }

    public String prefix() {
        return "order";
    }

    @Override
    public String total() {
        return repository.query();
    }

    public String findAll() {
        return repository.query();
    }
}
